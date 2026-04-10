"""
Вуди для Laikai — AI-агент для насмотренности.
Анализирует видео и сохраняет в Supabase.
+ Обновление статусов роликов (6 этапов).
"""
import os
import re
import json
import tempfile
import logging
import asyncio
from datetime import time, datetime
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
# content_analyzer больше не используется — экономим токены GPT-4o
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

    # Кнопка "Только текст" — транскрибация без анализа
    keyboard.append([InlineKeyboardButton("📝 Только текст", callback_data="transcribe_only")])

    return InlineKeyboardMarkup(keyboard)


async def handle_analyze_video(url: str, client_id: str, status_callback) -> str:
    """Скачивает видео, транскрибирует и сохраняет в Supabase."""

    client_info = supabase.get_client_by_id(client_id)
    client_name = client_info['name'] if client_info else "Клиент"

    await status_callback(f"⏳ Скачиваю и транскрибирую...\n📁 Клиент: {client_name}")

    try:
        video_info = analyze_video(url)
        transcript = video_info.get('transcript', '')

        # Хук = первое предложение транскрипции
        hook = transcript.split('.')[0].strip() if transcript else ''

        # Сохраняем в Supabase (без GPT-анализа)
        analysis = {
            'topic': hook[:50] if hook else 'Без названия',
            'hook': hook,
            'format': '', 'visp': '', 'visp_details': '',
            'problem': '', 'hunt_level': '', 'retention': '',
            'cta': '', 'idea_for_adaptation': '',
        }
        supabase.add_video_analysis(client_id, video_info, analysis)

        response = f"✅ Готово! Клиент: {client_name}\n\n"
        response += f"Хук: {hook}\n\n"
        response += f"Текст ролика:\n{transcript}"

        return response

    except Exception as e:
        logger.error(f"[AGENT] Ошибка: {e}")
        return f"😕 Не получилось: {str(e)[:100]}\n\nПопробуй другую ссылку?"


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

    # Пробуем распарсить как обновление статуса
    handled = await handle_status_reply(update, context)
    if handled:
        return

    # Обычное сообщение — подсказка
    await safe_reply(update.message,
        "Не понял. Вот что я умею:\n\n"
        "📹 Скинь ссылку на видео — транскрибирую\n"
        "📊 /status — статус роликов\n"
        "⚡ /s Женя 4 снято — быстрый статус\n"
        "🔗 /link Женя 3 https://... — ссылка на ролик"
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки."""
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "confirm_status":
        pending = context.user_data.get('pending_status_updates', [])
        if not pending:
            await query.edit_message_text("Нет данных для обновления.")
            return

        results = []
        for upd in pending:
            ok = supabase.update_topic_status(upd['topic_id'], upd['status'])
            emoji = STATUS_EMOJI[upd['status']]
            if ok:
                results.append(f"{emoji} {upd['client_name']} #{upd['number']} → {STATUS_LABEL[upd['status']]}")
            else:
                results.append(f"❌ {upd['client_name']} #{upd['number']} — ошибка")

        context.user_data['pending_status_updates'] = []
        await query.edit_message_text("Обновлено:\n" + "\n".join(results))
        return

    if data == "cancel_status":
        context.user_data['pending_status_updates'] = []
        await query.edit_message_text("Ок, жду новую формулировку.")
        return

    if data == "transcribe_only":
        # Только транскрибация — без анализа и без сохранения
        pending_url = context.user_data.get('pending_url')

        if not pending_url:
            await query.edit_message_text("😕 Ссылка потерялась. Скинь ещё раз.")
            return

        context.user_data['pending_url'] = None

        await query.edit_message_text("⏳ Скачиваю и транскрибирую...")

        try:
            video_info = analyze_video(pending_url)
            transcript = video_info.get('transcript', '')

            if not transcript:
                await query.edit_message_text("😕 Не удалось извлечь текст из видео.")
                return

            # Telegram ограничение — 4096 символов на сообщение
            header = "📝 Транскрибация:\n\n"
            max_len = 4096 - len(header) - 10

            if len(transcript) <= max_len:
                await query.edit_message_text(header + transcript)
            else:
                # Отправляем первую часть как edit, остальное — новыми сообщениями
                await query.edit_message_text(header + transcript[:max_len] + "...")
                remaining = transcript[max_len:]
                while remaining:
                    chunk = remaining[:4090]
                    remaining = remaining[4090:]
                    await query.message.reply_text(chunk)

        except Exception as e:
            logger.error(f"[TRANSCRIBE] Ошибка: {e}")
            await query.edit_message_text(f"😕 Не получилось транскрибировать: {str(e)[:100]}")

        return

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

        # Без parse_mode — в транскрипции могут быть спецсимволы
        if len(response) <= 4096:
            await query.edit_message_text(response)
        else:
            await query.edit_message_text(response[:4090] + "...")
            remaining = response[4090:]
            while remaining:
                chunk = remaining[:4090]
                remaining = remaining[4090:]
                await query.message.reply_text(chunk)


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


async def handle_track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обновляет просмотры всех видео клиентов."""
    status_msg = await update.message.reply_text("📊 Начинаю трекинг просмотров...")

    videos = supabase.get_all_videos_for_tracking()

    if not videos:
        await status_msg.edit_text("😕 Нет видео для трекинга.")
        return

    updated = 0
    errors = 0

    for i, video in enumerate(videos):
        try:
            url = video.get('link', '')
            if not url:
                continue

            # Получаем актуальные просмотры
            from video_analyzer import get_video_info_only
            info = get_video_info_only(url)

            new_views = info.get('view_count', 0)
            if new_views and new_views > 0:
                supabase.update_video_views(video['id'], new_views)
                updated += 1

            # Обновляем статус каждые 5 видео
            if (i + 1) % 5 == 0:
                await status_msg.edit_text(
                    f"📊 Трекинг... {i + 1}/{len(videos)}\n✅ Обновлено: {updated}"
                )

        except Exception as e:
            logger.error(f"[TRACK] Ошибка для {video.get('link', '')}: {e}")
            errors += 1
            continue

    await status_msg.edit_text(
        f"✅ **Трекинг завершён!**\n\n"
        f"📹 Всего видео: {len(videos)}\n"
        f"✅ Обновлено: {updated}\n"
        f"❌ Ошибок: {errors}"
    , parse_mode='Markdown')


# ==================== СТАТУСЫ РОЛИКОВ ====================

STATUS_EMOJI = {
    'idea': '💡', 'facts': '📋', 'script': '📝',
    'filmed': '🎬', 'edited': '🎞', 'published': '✅',
}

STATUS_KEYWORDS = {
    'idea':      ['идея', 'идеи', 'выбрали', 'отобрали', 'утвердили идеи'],
    'facts':     ['фактура', 'фактуру', 'собрал фактуру', 'прислал фактуру'],
    'script':    ['сценарий', 'написал', 'написали', 'скрипт'],
    'filmed':    ['сняли', 'снято', 'съёмка', 'съемка', 'записали'],
    'edited':    ['смонтировали', 'монтаж готов', 'монтаж сделан', 'смонтировано'],
    'published': ['выложили', 'выложен', 'опубликовали', 'опубликован'],
}

STATUS_LABEL = {
    'idea': 'идея', 'facts': 'фактура', 'script': 'сценарий',
    'filmed': 'снято', 'edited': 'смонтировано', 'published': 'выложено',
}

PRODUCER_CHAT_ID = os.getenv("PRODUCER_CHAT_ID", "107783646")


def format_status_message(clients_data: list) -> str:
    """Форматирует сообщение со статусами всех клиентов."""
    now = datetime.now()
    month_name = now.strftime('%B').lower()
    # Русские названия месяцев
    ru_months = {
        'january': 'январь', 'february': 'февраль', 'march': 'март',
        'april': 'апрель', 'may': 'май', 'june': 'июнь',
        'july': 'июль', 'august': 'август', 'september': 'сентябрь',
        'october': 'октябрь', 'november': 'ноябрь', 'december': 'декабрь',
    }
    month_ru = ru_months.get(month_name, month_name)

    lines = [f"📊 Статус на сегодня ({month_ru}):\n"]

    for c in clients_data:
        total = c['total']
        pub = c['published']
        counts = c['counts']

        # Формат: 💡8 📋6 📝5 🎬4 🎞3 ✅3
        counts_str = " ".join(
            f"{STATUS_EMOJI[s]}{counts.get(s, 0)}"
            for s in ['idea', 'facts', 'script', 'filmed', 'edited', 'published']
        )

        lines.append(f"{c['name']} — {pub}/{total} выложено")
        lines.append(f"  {counts_str}")
        lines.append("")

    lines.append("Что изменилось? Ответь текстом или голосовым.")
    return "\n".join(lines)


def parse_status_updates(text: str) -> list:
    """
    Парсит текстовое сообщение и извлекает обновления статусов.
    Возвращает: [{client_name, numbers, status}]
    """
    updates = []
    text_lower = text.lower()

    # Определяем статус по ключевым словам
    detected_status = None
    for status, keywords in STATUS_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                detected_status = status
                break
        if detected_status:
            break

    if not detected_status:
        return []

    # Ищем номера роликов
    numbers = [int(n) for n in re.findall(r'\b(\d{1,2})\b', text) if 1 <= int(n) <= 20]

    if not numbers:
        return []

    # Ищем имя клиента — берём все слова с заглавной буквы (не в начале предложения)
    # или слова после "по", "у", "для"
    name_patterns = [
        r'(?:по|у|для|клиент[а]?)\s+([А-ЯЁа-яё]+)',
        r'([А-ЯЁ][а-яё]{2,})',
    ]

    client_name = None
    for pattern in name_patterns:
        match = re.search(pattern, text)
        if match:
            candidate = match.group(1)
            # Исключаем ключевые слова статусов
            skip_words = {'сняли', 'снято', 'ролик', 'ролики', 'написал', 'написали',
                          'сценарий', 'монтаж', 'выложили', 'выложен', 'смонтировали',
                          'что', 'это', 'там', 'тут', 'еще', 'ещё', 'все', 'всё'}
            if candidate.lower() not in skip_words:
                client_name = candidate
                break

    if client_name:
        updates.append({
            'client_name': client_name,
            'numbers': numbers,
            'status': detected_status,
        })

    return updates


async def send_daily_status(context: ContextTypes.DEFAULT_TYPE):
    """Ежедневное сообщение в 20:00 МСК."""
    try:
        clients_data = supabase.get_all_clients_status()
        if not clients_data:
            return

        msg = format_status_message(clients_data)
        await context.bot.send_message(chat_id=PRODUCER_CHAT_ID, text=msg)
        logger.info("[STATUS] Ежедневный статус отправлен")
    except Exception as e:
        logger.error(f"[STATUS] Ошибка отправки: {e}")


async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/status — показать текущий статус."""
    try:
        clients_data = supabase.get_all_clients_status()
        if not clients_data:
            await update.message.reply_text("Нет данных по клиентам.")
            return
        msg = format_status_message(clients_data)
        await update.message.reply_text(msg)
    except Exception as e:
        logger.error(f"[STATUS] Ошибка: {e}")
        await update.message.reply_text(f"Ошибка: {str(e)[:100]}")


async def handle_quick_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/s Женя 4 снято — быстрое обновление статуса."""
    args = context.args
    if not args or len(args) < 3:
        await update.message.reply_text("Формат: /s [имя] [номер] [статус]\nПример: /s Женя 4 снято")
        return

    name_part = args[0]
    status_word = args[-1].lower()

    # Собираем номера из середины
    numbers = [int(a) for a in args[1:-1] if a.isdigit()]
    if not numbers:
        await update.message.reply_text("Не нашёл номер ролика. Пример: /s Женя 4 снято")
        return

    # Находим статус
    detected_status = None
    for status, keywords in STATUS_KEYWORDS.items():
        if status_word in keywords or status_word == status:
            detected_status = status
            break
    # Ещё попробуем по лейблу
    if not detected_status:
        for status, label in STATUS_LABEL.items():
            if status_word == label:
                detected_status = status
                break

    if not detected_status:
        await update.message.reply_text(
            f"Не понял статус '{status_word}'.\n"
            f"Допустимые: идея, фактура, сценарий, снято, смонтировано, выложено"
        )
        return

    # Находим клиента
    found_client = supabase.find_client_by_name(name_part)
    if not found_client:
        await update.message.reply_text(f"Не нашёл клиента '{name_part}'")
        return

    # Обновляем
    results = []
    for num in numbers:
        topic = supabase.find_topic_by_number(found_client['id'], num)
        if topic:
            ok = supabase.update_topic_status(topic['id'], detected_status)
            emoji = STATUS_EMOJI[detected_status]
            if ok:
                results.append(f"{emoji} #{num} {topic['title']} → {STATUS_LABEL[detected_status]}")
            else:
                results.append(f"❌ #{num} ошибка обновления")
        else:
            results.append(f"❌ #{num} не найден")

    response = f"Обновлено для {found_client['name']}:\n" + "\n".join(results)
    await update.message.reply_text(response)


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/link Женя 3 https://... — ролик выложен + ссылка."""
    args = context.args
    if not args or len(args) < 3:
        await update.message.reply_text("Формат: /link [имя] [номер] [ссылка]\nПример: /link Женя 3 https://...")
        return

    name_part = args[0]

    # Находим номер
    number = None
    link = None
    for a in args[1:]:
        if a.isdigit():
            number = int(a)
        elif a.startswith('http'):
            link = a

    if not number or not link:
        await update.message.reply_text("Не нашёл номер ролика или ссылку.")
        return

    found_client = supabase.find_client_by_name(name_part)
    if not found_client:
        await update.message.reply_text(f"Не нашёл клиента '{name_part}'")
        return

    topic = supabase.find_topic_by_number(found_client['id'], number)
    if not topic:
        await update.message.reply_text(f"Ролик #{number} не найден у {found_client['name']}")
        return

    ok = supabase.update_topic_link(topic['id'], link)
    if ok:
        await update.message.reply_text(
            f"✅ {found_client['name']} — #{number} {topic['title']}\n"
            f"Статус: выложено\nСсылка сохранена"
        )
    else:
        await update.message.reply_text("Ошибка сохранения.")


async def handle_status_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Проверяет, является ли сообщение ответом на статус-запрос.
    Возвращает True если обработано, False если нет.
    """
    text = update.message.text or ''
    if not text:
        return False

    updates = parse_status_updates(text)
    if not updates:
        return False

    # Формируем подтверждение
    confirm_lines = ["Правильно понял?\n"]
    pending_updates = []

    for upd in updates:
        found_client = supabase.find_client_by_name(upd['client_name'])
        if not found_client:
            confirm_lines.append(f"❌ Не нашёл клиента '{upd['client_name']}'")
            continue

        emoji = STATUS_EMOJI[upd['status']]
        nums_str = ", ".join(str(n) for n in upd['numbers'])
        confirm_lines.append(
            f"{emoji} {found_client['name']} — ролик {nums_str} → {STATUS_LABEL[upd['status']]}"
        )

        for num in upd['numbers']:
            topic = supabase.find_topic_by_number(found_client['id'], num)
            if topic:
                pending_updates.append({
                    'topic_id': topic['id'],
                    'topic_title': topic['title'],
                    'status': upd['status'],
                    'client_name': found_client['name'],
                    'number': num,
                })

    if not pending_updates:
        return False

    # Сохраняем pending для подтверждения
    context.user_data['pending_status_updates'] = pending_updates

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Да", callback_data="confirm_status"),
            InlineKeyboardButton("❌ Нет, переформулирую", callback_data="cancel_status"),
        ]
    ])

    await update.message.reply_text("\n".join(confirm_lines), reply_markup=keyboard)
    return True


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
    application.add_handler(CommandHandler("track", handle_track))
    application.add_handler(CommandHandler("status", handle_status))
    application.add_handler(CommandHandler("s", handle_quick_status))
    application.add_handler(CommandHandler("link", handle_link))

    # Callback от кнопок
    application.add_handler(CallbackQueryHandler(handle_callback))

    # Текст и голосовые
    application.add_handler(MessageHandler(
        filters.TEXT | filters.VOICE,
        handle_message
    ))

    # Ежедневное сообщение в 20:00 МСК (17:00 UTC)
    job_queue = application.job_queue
    if job_queue:
        job_queue.run_daily(
            send_daily_status,
            time=time(hour=17, minute=0),  # 17:00 UTC = 20:00 МСК
            name="daily_status",
        )
        logger.info("[STATUS] Ежедневный статус запланирован на 20:00 МСК")

    logger.info("Вуди для Laikai запущен! 🤠")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
