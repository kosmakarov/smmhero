"""
Вуди для Laikai — AI-агент для насмотренности.
Анализирует видео и сохраняет в Supabase.
"""
import os
import re
import json
import tempfile
import logging
import asyncio
from pathlib import Path
from dotenv import load_dotenv

from openai import OpenAI
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from telegram.error import TimedOut, NetworkError

from video_analyzer import analyze_video, detect_platform
from content_analyzer import analyze_content
from supabase_service import SupabaseManager

load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# OpenAI клиент
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Supabase
supabase = SupabaseManager()

# Системный промпт
AGENT_SYSTEM_PROMPT = """Ты — Вуди, AI-ассистент для контент-маркетингового агентства.

ТВОИ ВОЗМОЖНОСТИ:
1. Анализировать видео (Reels, Shorts, TikTok) — скачиваешь, транскрибируешь, анализируешь
2. Добавлять анализ в таблицу насмотренности клиента в Laikai
3. Отвечать на вопросы о контенте

КЛИЕНТЫ В АГЕНТСТВЕ: {clients}

ТВОЙ СТИЛЬ:
- Говоришь кратко и по делу
- Дружелюбный, но профессиональный

Отвечай на русском языке."""


async def safe_reply(message, text, parse_mode=None, reply_markup=None, retries=3):
    """Отправляет сообщение с повторами при ошибке сети."""
    for attempt in range(retries):
        try:
            return await message.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
        except (TimedOut, NetworkError) as e:
            if attempt < retries - 1:
                logger.warning(f"[NETWORK] Retry {attempt + 1}/{retries}: {e}")
                await asyncio.sleep(2)
            else:
                raise


async def safe_edit(message, text, parse_mode=None, reply_markup=None, retries=3):
    """Редактирует сообщение с повторами."""
    for attempt in range(retries):
        try:
            return await message.edit_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
        except (TimedOut, NetworkError) as e:
            if attempt < retries - 1:
                await asyncio.sleep(2)
            else:
                raise


def extract_url(text: str) -> str:
    """Извлекает URL из текста."""
    url_pattern = r'https?://[^\s]+'
    match = re.search(url_pattern, text)
    return match.group(0) if match else ""


def is_video_url(url: str) -> bool:
    """Проверяет, является ли URL ссылкой на видео."""
    if not url:
        return False
    patterns = [
        r'youtube\.com/shorts',
        r'youtu\.be',
        r'instagram\.com/reel',
        r'instagram\.com/p/',
        r'tiktok\.com',
        r'vk\.com/video',
        r'vk\.com/clip',
    ]
    return any(re.search(p, url.lower()) for p in patterns)


async def transcribe_voice(voice_file_path: str) -> str:
    """Транскрибирует голосовое сообщение."""
    logger.info(f"[VOICE] Транскрибирую: {voice_file_path}")

    with open(voice_file_path, 'rb') as audio:
        response = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio,
            language="ru"
        )

    return response.text


def get_clients_keyboard() -> InlineKeyboardMarkup:
    """Создаёт клавиатуру с клиентами."""
    clients = supabase.get_clients()

    if not clients:
        return None

    # Создаём кнопки по 2 в ряд
    keyboard = []
    row = []
    for client in clients:
        btn = InlineKeyboardButton(
            client['name'],
            callback_data=f"client:{client['id']}"
        )
        row.append(btn)
        if len(row) == 2:
            keyboard.append(row)
            row = []

    if row:  # Оставшиеся кнопки
        keyboard.append(row)

    return InlineKeyboardMarkup(keyboard)


async def handle_analyze_video(url: str, client_id: str, status_callback) -> str:
    """Анализирует видео и сохраняет в Supabase."""

    # Получаем информацию о клиенте
    client_info = supabase.get_client_by_id(client_id)
    client_name = client_info['name'] if client_info else "Клиент"

    await status_callback(f"⏳ Скачиваю и анализирую видео...\n📁 Клиент: {client_name}")

    try:
        # Анализируем видео
        video_info = analyze_video(url)

        await status_callback(f"⏳ Транскрибировал, анализирую контент...")

        # Анализируем через GPT
        analysis = analyze_content(
            transcript=video_info.get('transcript', ''),
            video_info=video_info,
            niche=client_info.get('niche', '') if client_info else '',
        )

        # Сохраняем в Supabase
        result = supabase.add_video_analysis(client_id, video_info, analysis)

        # Формируем ответ
        response = f"""✅ Готово!

📹 **{analysis.get('topic', 'Видео')}**
👤 Клиент: {client_name}

**Почему залетело (ВИСП):**
{analysis.get('visp', 'N/A')}

**Хук (первые 3 сек):**
_{analysis.get('hook', 'N/A')}_

**Что держит до конца:**
{analysis.get('retention', 'N/A')}"""

        if result:
            response += "\n\n📊 Добавлено в Laikai"

        return response

    except Exception as e:
        logger.error(f"[AGENT] Ошибка анализа: {e}")
        return f"😕 Не получилось проанализировать: {str(e)[:100]}\n\nПопробуй другую ссылку?"


async def handle_chat(user_message: str) -> str:
    """Обрабатывает обычное общение."""
    clients = supabase.get_clients()
    client_names = [c['name'] for c in clients]

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": AGENT_SYSTEM_PROMPT.format(clients=client_names)},
            {"role": "user", "content": user_message}
        ],
        temperature=0.7,
        max_tokens=500
    )

    return response.choices[0].message.content


# ==================== TELEGRAM HANDLERS ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приветствие."""
    user = update.effective_user

    # Получаем клиентов
    clients = supabase.get_clients()
    clients_list = ", ".join([c['name'] for c in clients]) if clients else "пока нет"

    welcome = f"""🤠 Йоу, {user.first_name}! Я Вуди.

Я помогаю анализировать Reels и Shorts и добавлять их в Laikai.

**Как работать:**
1. Скинь ссылку на видео
2. Выбери клиента из списка
3. Я проанализирую и добавлю в насмотренность

**Клиенты:** {clients_list}

Просто кидай ссылку! 🚀"""

    await update.message.reply_text(welcome, parse_mode='Markdown')


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Главный обработчик сообщений."""
    user = update.effective_user

    # Инициализируем user_data
    if 'pending_url' not in context.user_data:
        context.user_data['pending_url'] = None

    # Получаем текст (из обычного сообщения или голосового)
    if update.message.voice:
        voice = await update.message.voice.get_file()

        with tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as f:
            await voice.download_to_drive(f.name)
            voice_path = f.name

        status_msg = await update.message.reply_text("🎤 Слушаю...")

        try:
            user_text = await transcribe_voice(voice_path)
            await status_msg.edit_text(f"🎤 Понял: _{user_text}_", parse_mode='Markdown')
        finally:
            os.unlink(voice_path)
    else:
        user_text = update.message.text
        status_msg = None

    logger.info(f"[{user.username}] {user_text[:50]}...")

    # Проверяем на URL
    url = extract_url(user_text)

    if is_video_url(url):
        # Сохраняем URL и показываем выбор клиента
        context.user_data['pending_url'] = url

        keyboard = get_clients_keyboard()

        if keyboard:
            await update.message.reply_text(
                f"📹 Нашёл ссылку!\n\n👤 Для какого клиента анализируем?",
                reply_markup=keyboard
            )
        else:
            await update.message.reply_text(
                "😕 В Laikai пока нет клиентов. Сначала добавь клиента в системе."
            )
        return

    # Обычное сообщение — чат
    response = await handle_chat(user_text)
    await safe_reply(update.message, response, parse_mode='Markdown')


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки."""
    query = update.callback_query
    await query.answer()

    data = query.data

    if data.startswith("client:"):
        client_id = data.split(":")[1]

        # Получаем сохранённый URL
        pending_url = context.user_data.get('pending_url')

        if not pending_url:
            await query.edit_message_text("😕 Ссылка потерялась. Скинь ещё раз.")
            return

        context.user_data['pending_url'] = None

        # Создаём callback для обновления статуса
        async def status_callback(text):
            try:
                await query.edit_message_text(text)
            except Exception:
                pass

        # Анализируем
        response = await handle_analyze_video(pending_url, client_id, status_callback)

        await query.edit_message_text(response, parse_mode='Markdown')


async def handle_clients(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает список клиентов."""
    clients = supabase.get_clients()

    if not clients:
        await update.message.reply_text("😕 В Laikai пока нет клиентов.")
        return

    text = "👥 **Клиенты в Laikai:**\n\n"
    for c in clients:
        text += f"• {c['name']}\n"

    await update.message.reply_text(text, parse_mode='Markdown')


# ==================== MAIN ====================

def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")

    if not token:
        logger.error("TELEGRAM_BOT_TOKEN не найден!")
        return

    if not os.getenv("OPENAI_API_KEY"):
        logger.error("OPENAI_API_KEY не найден!")
        return

    if not os.getenv("SUPABASE_URL") or not os.getenv("SUPABASE_ANON_KEY"):
        logger.error("SUPABASE_URL и SUPABASE_ANON_KEY не найдены!")
        return

    logger.info("=" * 50)
    logger.info("🤠 Вуди для Laikai v1.0")
    logger.info("=" * 50)

    from telegram.request import HTTPXRequest

    request = HTTPXRequest(
        connect_timeout=20.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0,
    )

    application = (
        Application.builder()
        .token(token)
        .request(request)
        .get_updates_request(request)
        .build()
    )

    # Команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("clients", handle_clients))

    # Callback от кнопок
    application.add_handler(CallbackQueryHandler(handle_callback))

    # Текст и голосовые
    application.add_handler(MessageHandler(
        filters.TEXT | filters.VOICE,
        handle_message
    ))

    logger.info("Вуди для Laikai запущен! 🤠")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
