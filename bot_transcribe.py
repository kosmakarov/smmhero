"""
Вуди (упрощённый) — бот для транскрибации.
Принимает ссылки на Reels/Shorts/TikTok/YouTube/VK, видео-файлы и голосовые.
Возвращает текстовую расшифровку. И всё. Без баз и клиентов.
"""
import os
import re
import tempfile
import logging
import asyncio

from dotenv import load_dotenv
from openai import OpenAI
from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    CommandHandler,
    filters,
    ContextTypes,
)
from telegram.error import TimedOut, NetworkError

from video_analyzer import analyze_video

load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

VIDEO_URL_PATTERNS = [
    r'youtube\.com/shorts', r'youtube\.com/watch', r'youtu\.be',
    r'instagram\.com/reel', r'instagram\.com/p/', r'instagram\.com/tv',
    r'tiktok\.com', r'vk\.com/video', r'vk\.com/clip',
]


# ── Утилиты ─────────────────────────────────────────────────

async def safe_reply(message, text, **kwargs):
    for attempt in range(3):
        try:
            return await message.reply_text(text, **kwargs)
        except (TimedOut, NetworkError):
            if attempt < 2:
                await asyncio.sleep(2)


def extract_url(text: str) -> str | None:
    if not text:
        return None
    m = re.search(r'https?://\S+', text)
    return m.group(0) if m else None


def is_video_url(url: str | None) -> bool:
    if not url:
        return False
    u = url.lower()
    return any(re.search(p, u) for p in VIDEO_URL_PATTERNS)


async def send_transcript(message, transcript: str):
    """Отправляет транскрипт, дробя на куски если длинный."""
    header = "📝 Транскрипт:\n\n"
    full = header + transcript
    if len(full) <= 4096:
        await message.reply_text(full)
        return
    first = full[:4090]
    await message.reply_text(first)
    remaining = full[4090:]
    while remaining:
        chunk = remaining[:4090]
        remaining = remaining[4090:]
        await message.reply_text(chunk)


def transcribe_audio_file(file_path: str) -> str:
    """Прогоняет аудио/видео-файл через Whisper (sync, вызывать через to_thread).
    Локальный faster-whisper в приоритете, OpenAI — резерв."""
    from local_whisper import transcribe
    return transcribe(file_path, language="ru")


# ── Команды ─────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤠 Я Вуди. Делаю одну вещь — превращаю видео в текст.\n\n"
        "Как пользоваться:\n"
        "📹 Скинь ссылку на Reels / Shorts / TikTok / YouTube / VK\n"
        "🎵 Или пришли видео/аудио файлом\n"
        "🎤 Или голосовое сообщение\n\n"
        "Верну расшифровку."
    )


# ── Обработчики ─────────────────────────────────────────────

async def handle_url(update: Update, url: str):
    status_msg = await update.message.reply_text("⏳ Скачиваю и транскрибирую...")
    try:
        info = await asyncio.to_thread(analyze_video, url)
        transcript = (info or {}).get('transcript', '') or ''
        if not transcript.strip():
            await status_msg.edit_text("😕 Не удалось вытащить текст из этого видео.")
            return
        await status_msg.edit_text("✅ Готово")
        await send_transcript(update.message, transcript)
    except Exception as e:
        logger.exception("URL transcription failed")
        await status_msg.edit_text(f"😕 Не получилось: {str(e)[:200]}")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    voice = await update.message.voice.get_file()
    status_msg = await update.message.reply_text("🎤 Слушаю...")
    with tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as f:
        await voice.download_to_drive(f.name)
        path = f.name
    try:
        text = await asyncio.to_thread(transcribe_audio_file, path)
        if not text.strip():
            await status_msg.edit_text("😕 Тишина — ничего не распознал.")
            return
        await status_msg.edit_text("✅ Готово")
        await send_transcript(update.message, text)
    except Exception as e:
        logger.exception("Voice transcription failed")
        await status_msg.edit_text(f"😕 Не получилось: {str(e)[:200]}")
    finally:
        try:
            os.unlink(path)
        except Exception:
            pass


async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    file_obj = None
    suffix = ''
    if msg.video:
        file_obj = await msg.video.get_file()
        suffix = '.mp4'
    elif msg.audio:
        file_obj = await msg.audio.get_file()
        mime = (msg.audio.mime_type or '').split('/')[-1] or 'mp3'
        suffix = f'.{mime}'
    elif msg.video_note:
        file_obj = await msg.video_note.get_file()
        suffix = '.mp4'
    elif msg.document:
        name = (msg.document.file_name or '').lower()
        if any(name.endswith(ext) for ext in ['.mp4', '.mov', '.mp3', '.m4a', '.wav', '.ogg', '.webm']):
            file_obj = await msg.document.get_file()
            suffix = os.path.splitext(name)[1] or '.mp4'

    if not file_obj:
        return

    status_msg = await msg.reply_text("⏳ Скачиваю и транскрибирую...")
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        await file_obj.download_to_drive(f.name)
        path = f.name
    try:
        text = await asyncio.to_thread(transcribe_audio_file, path)
        if not text.strip():
            await status_msg.edit_text("😕 Текста нет.")
            return
        await status_msg.edit_text("✅ Готово")
        await send_transcript(msg, text)
    except Exception as e:
        logger.exception("Media transcription failed")
        await status_msg.edit_text(f"😕 Не получилось: {str(e)[:200]}")
    finally:
        try:
            os.unlink(path)
        except Exception:
            pass


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ''
    url = extract_url(text)
    if is_video_url(url):
        await handle_url(update, url)
        return
    if url:
        await update.message.reply_text(
            "🤔 Эта ссылка не похожа на Reels/Shorts/TikTok/YouTube/VK. "
            "Если это видео — пришли его файлом."
        )
        return
    await update.message.reply_text(
        "🤠 Кинь ссылку на видео, файл или голосовое — расшифрую.\n"
        "/start — короткая инструкция."
    )


# ── Main ────────────────────────────────────────────────────

def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN не задан")
        return
    if not os.getenv("OPENAI_API_KEY"):
        logger.error("OPENAI_API_KEY не задан")
        return

    logger.info("=" * 50)
    logger.info("🤠 Wuddy транскрибатор v2.0 (только расшифровка)")
    logger.info("=" * 50)

    from telegram.request import HTTPXRequest
    request = HTTPXRequest(
        connect_timeout=20.0,
        read_timeout=60.0,
        write_timeout=60.0,
        pool_timeout=30.0,
    )
    application = (
        Application.builder()
        .token(token)
        .request(request)
        .get_updates_request(request)
        .build()
    )

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_start))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))
    application.add_handler(MessageHandler(
        filters.VIDEO | filters.AUDIO | filters.VIDEO_NOTE | filters.Document.ALL,
        handle_media,
    ))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Готов")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
