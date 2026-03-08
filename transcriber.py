"""
Модуль для транскрибации длинных аудио/видео.
Поддерживает: YouTube, загруженные файлы, разбивка на части.
"""
import os
import tempfile
import logging
import subprocess
from pathlib import Path

import yt_dlp
from openai import OpenAI
from dotenv import load_dotenv
from pydub import AudioSegment

load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Whisper лимит ~25MB, берём с запасом
MAX_CHUNK_SIZE_MB = 20
MAX_CHUNK_DURATION_MS = 10 * 60 * 1000  # 10 минут в миллисекундах


def download_audio_from_url(url: str) -> tuple[str, dict]:
    """
    Скачивает аудио с YouTube или другого сервиса.

    Returns:
        tuple: (путь_к_файлу, метаданные)
    """
    logger.info(f"[TRANSCRIBER] Скачивание аудио: {url}")

    temp_dir = tempfile.mkdtemp()
    output_template = os.path.join(temp_dir, '%(id)s.%(ext)s')

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': output_template,
        'quiet': True,
        'no_warnings': True,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '64',  # Низкое качество для экономии места
        }],
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

            metadata = {
                'title': info.get('title', 'Без названия'),
                'duration': info.get('duration', 0),
                'uploader': info.get('uploader', ''),
                'url': url,
            }

            # Находим скачанный файл
            audio_file = os.path.join(temp_dir, f"{info['id']}.mp3")

            if not os.path.exists(audio_file):
                for f in os.listdir(temp_dir):
                    if f.endswith(('.mp3', '.m4a', '.wav', '.webm')):
                        audio_file = os.path.join(temp_dir, f)
                        break

            logger.info(f"[TRANSCRIBER] Скачано: {audio_file}")
            return audio_file, metadata

    except Exception as e:
        logger.error(f"[TRANSCRIBER] Ошибка скачивания: {e}")
        raise


def convert_to_mp3(input_path: str) -> str:
    """Конвертирует аудио/видео в MP3."""
    logger.info(f"[TRANSCRIBER] Конвертация в MP3: {input_path}")

    output_path = input_path.rsplit('.', 1)[0] + '_converted.mp3'

    cmd = [
        'ffmpeg', '-i', input_path,
        '-vn',  # Без видео
        '-acodec', 'libmp3lame',
        '-ab', '64k',  # Низкий битрейт для экономии
        '-ar', '16000',  # 16kHz достаточно для речи
        '-y',  # Перезаписать если есть
        output_path
    ]

    try:
        subprocess.run(cmd, capture_output=True, check=True)
        logger.info(f"[TRANSCRIBER] Сконвертировано: {output_path}")
        return output_path
    except subprocess.CalledProcessError as e:
        logger.error(f"[TRANSCRIBER] Ошибка конвертации: {e}")
        raise


def split_audio(audio_path: str) -> list[str]:
    """
    Разбивает длинное аудио на части по 10 минут.

    Returns:
        list: Список путей к частям
    """
    logger.info(f"[TRANSCRIBER] Проверяю размер: {audio_path}")

    file_size_mb = os.path.getsize(audio_path) / (1024 * 1024)

    # Если файл маленький — не разбиваем
    if file_size_mb <= MAX_CHUNK_SIZE_MB:
        logger.info(f"[TRANSCRIBER] Файл {file_size_mb:.1f}MB — разбивка не нужна")
        return [audio_path]

    logger.info(f"[TRANSCRIBER] Файл {file_size_mb:.1f}MB — разбиваю на части...")

    # Загружаем аудио
    audio = AudioSegment.from_file(audio_path)
    duration_ms = len(audio)

    chunks = []
    temp_dir = os.path.dirname(audio_path)

    start = 0
    part_num = 1

    while start < duration_ms:
        end = min(start + MAX_CHUNK_DURATION_MS, duration_ms)
        chunk = audio[start:end]

        chunk_path = os.path.join(temp_dir, f"part_{part_num:02d}.mp3")
        chunk.export(chunk_path, format="mp3", bitrate="64k")
        chunks.append(chunk_path)

        logger.info(f"[TRANSCRIBER] Часть {part_num}: {start//1000//60}:{start//1000%60:02d} - {end//1000//60}:{end//1000%60:02d}")

        start = end
        part_num += 1

    logger.info(f"[TRANSCRIBER] Разбито на {len(chunks)} частей")
    return chunks


def transcribe_audio_file(audio_path: str, language: str = "ru") -> str:
    """Транскрибирует один аудио файл через Whisper."""
    logger.info(f"[WHISPER] Транскрибирую: {audio_path}")

    with open(audio_path, 'rb') as audio_file:
        response = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            language=language,
            response_format="text"
        )

    return response


def transcribe_long_audio(audio_path: str, language: str = "ru", progress_callback=None) -> str:
    """
    Транскрибирует длинное аудио с разбивкой на части.

    Args:
        audio_path: Путь к аудио файлу
        language: Язык (ru, en, etc.)
        progress_callback: Функция для обновления прогресса

    Returns:
        str: Полная транскрипция
    """
    # Разбиваем на части если нужно
    chunks = split_audio(audio_path)

    transcripts = []
    total_chunks = len(chunks)

    for i, chunk_path in enumerate(chunks, 1):
        if progress_callback:
            progress_callback(f"Транскрибирую часть {i}/{total_chunks}...")

        logger.info(f"[TRANSCRIBER] Обрабатываю часть {i}/{total_chunks}")

        transcript = transcribe_audio_file(chunk_path, language)
        transcripts.append(transcript)

        # Удаляем временную часть (но не оригинал)
        if chunk_path != audio_path:
            try:
                os.remove(chunk_path)
            except:
                pass

    # Объединяем
    full_transcript = "\n\n".join(transcripts)
    logger.info(f"[TRANSCRIBER] Готово: {len(full_transcript)} символов")

    return full_transcript


async def transcribe_from_url(url: str, progress_callback=None) -> dict:
    """
    Скачивает и транскрибирует видео/аудио по URL.

    Returns:
        dict: {
            'title': str,
            'duration': int,
            'transcript': str,
            'url': str
        }
    """
    audio_path = None

    try:
        if progress_callback:
            await progress_callback("Скачиваю аудио...")

        audio_path, metadata = download_audio_from_url(url)

        # Определяем язык по метаданным (можно улучшить)
        language = "ru"

        def sync_progress(text):
            # Wrapper для синхронного вызова
            import asyncio
            if progress_callback:
                asyncio.create_task(progress_callback(text))

        if progress_callback:
            await progress_callback("Транскрибирую...")

        transcript = transcribe_long_audio(audio_path, language)

        return {
            'title': metadata.get('title', 'Без названия'),
            'duration': metadata.get('duration', 0),
            'transcript': transcript,
            'url': url,
        }

    finally:
        # Чистим временные файлы
        if audio_path and os.path.exists(audio_path):
            try:
                os.remove(audio_path)
                os.rmdir(os.path.dirname(audio_path))
            except:
                pass


async def transcribe_from_file(file_path: str, progress_callback=None) -> dict:
    """
    Транскрибирует загруженный файл.

    Returns:
        dict: {
            'title': str,
            'transcript': str,
        }
    """
    converted_path = None

    try:
        # Определяем тип файла
        ext = Path(file_path).suffix.lower()

        # Если это видео или не mp3 — конвертируем
        if ext not in ['.mp3', '.wav', '.m4a']:
            if progress_callback:
                await progress_callback("Конвертирую в аудио...")
            converted_path = convert_to_mp3(file_path)
            audio_path = converted_path
        else:
            audio_path = file_path

        if progress_callback:
            await progress_callback("Транскрибирую...")

        transcript = transcribe_long_audio(audio_path)

        return {
            'title': Path(file_path).stem,
            'transcript': transcript,
        }

    finally:
        # Чистим конвертированный файл
        if converted_path and os.path.exists(converted_path):
            try:
                os.remove(converted_path)
            except:
                pass


# Тест
if __name__ == "__main__":
    import asyncio

    # Тест с YouTube
    test_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    async def test():
        result = await transcribe_from_url(test_url)
        print(f"Title: {result['title']}")
        print(f"Duration: {result['duration']} sec")
        print(f"Transcript: {result['transcript'][:500]}...")

    asyncio.run(test())
