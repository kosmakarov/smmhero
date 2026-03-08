"""
Модуль для скачивания и транскрипции видео (Reels, Shorts, TikTok)
"""
import os
import re
import tempfile
import logging
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
import instaloader
import requests
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def detect_platform(url: str) -> str:
    """Определяет платформу по URL."""
    url_lower = url.lower()

    if 'youtube.com/shorts' in url_lower or 'youtu.be' in url_lower:
        return 'youtube'
    elif 'instagram.com/reel' in url_lower or 'instagram.com/p/' in url_lower:
        return 'instagram'
    elif 'tiktok.com' in url_lower:
        return 'tiktok'
    elif 'vk.com' in url_lower or 'vk.ru' in url_lower:
        return 'vk'
    else:
        return 'unknown'


def extract_video_id(url: str, platform: str) -> str:
    """Извлекает ID видео из URL."""
    if platform == 'youtube':
        # YouTube Shorts: /shorts/VIDEO_ID
        match = re.search(r'/shorts/([a-zA-Z0-9_-]+)', url)
        if match:
            return match.group(1)
        # Обычный YouTube
        match = re.search(r'(?:v=|youtu\.be/)([a-zA-Z0-9_-]+)', url)
        if match:
            return match.group(1)

    elif platform == 'instagram':
        # Instagram: /reel/CODE/ или /p/CODE/
        match = re.search(r'/(?:reel|p)/([a-zA-Z0-9_-]+)', url)
        if match:
            return match.group(1)

    elif platform == 'tiktok':
        # TikTok: /video/VIDEO_ID
        match = re.search(r'/video/(\d+)', url)
        if match:
            return match.group(1)

    return url  # fallback


def get_instagram_view_count(shortcode: str) -> int:
    """
    Получает количество просмотров Instagram Reel через instaloader с логином.
    """
    try:
        username = os.getenv("INSTAGRAM_USERNAME", "")
        password = os.getenv("INSTAGRAM_PASSWORD", "")

        if not username or not password:
            logger.warning("[INSTAGRAM] INSTAGRAM_USERNAME и INSTAGRAM_PASSWORD не заданы")
            return 0

        L = instaloader.Instaloader()

        # Логинимся
        try:
            L.login(username, password)
            logger.info(f"[INSTAGRAM] Залогинились как {username}")
        except Exception as e:
            logger.error(f"[INSTAGRAM] Ошибка логина: {e}")
            return 0

        # Получаем пост
        post = instaloader.Post.from_shortcode(L.context, shortcode)

        # video_view_count для видео/reels
        view_count = post.video_view_count if post.is_video else post.likes
        logger.info(f"[INSTAGRAM] Просмотры: {view_count}")
        return view_count or 0

    except Exception as e:
        logger.warning(f"[INSTAGRAM] Ошибка получения просмотров: {e}")
        return 0


def get_instagram_cookies_file() -> str:
    """Создаёт временный файл с cookies из переменной окружения."""
    cookies_content = os.getenv("INSTAGRAM_COOKIES", "")
    if not cookies_content:
        return None

    # Создаём временный файл
    cookies_file = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
    cookies_file.write(cookies_content)
    cookies_file.close()
    return cookies_file.name


def download_instagram_reel(url: str) -> tuple[str, dict]:
    """
    Скачивает Instagram Reel через yt-dlp.
    """
    logger.info(f"[INSTAGRAM] Скачивание: {url}")

    # Извлекаем shortcode из URL
    match = re.search(r'/(?:reel|p)/([A-Za-z0-9_-]+)', url)
    if not match:
        raise ValueError("Не удалось извлечь код рилса из URL")

    shortcode = match.group(1)

    # Создаём временную директорию
    temp_dir = tempfile.mkdtemp()
    output_template = os.path.join(temp_dir, '%(id)s.%(ext)s')

    # Базовые опции
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': output_template,
        'quiet': True,
        'no_warnings': True,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '128',
        }],
    }

    # Пробуем с cookies если есть
    cookies_file = get_instagram_cookies_file()
    if cookies_file:
        logger.info("[INSTAGRAM] Использую cookies из переменной...")
        ydl_opts['cookiefile'] = cookies_file
    else:
        logger.info("[INSTAGRAM] Cookies не найдены, пробую без них...")

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

            # Обрабатываем дату
            upload_date = info.get('upload_date', '')
            if not upload_date and info.get('timestamp'):
                from datetime import datetime
                upload_date = datetime.fromtimestamp(info['timestamp']).strftime('%Y%m%d')

            # Instagram не отдаёт view_count, используем like_count как fallback
            view_count = info.get('view_count') or info.get('play_count', 0)
            like_count = info.get('like_count', 0)

            # Если нет просмотров — пробуем получить через instaloader
            if not view_count:
                try:
                    view_count = get_instagram_view_count(shortcode)
                except:
                    pass

            metadata = {
                'platform': 'instagram',
                'video_id': shortcode,
                'title': (info.get('title') or info.get('description', ''))[:100] or 'Instagram Reel',
                'description': info.get('description', ''),
                'duration': info.get('duration', 0),
                'view_count': view_count,
                'like_count': like_count,
                'comment_count': info.get('comment_count', 0),
                'uploader': info.get('uploader') or info.get('channel', ''),
                'upload_date': upload_date,
                'url': url,
            }

            # Находим скачанный файл
            audio_file = os.path.join(temp_dir, f"{info['id']}.mp3")

            if not os.path.exists(audio_file):
                for f in os.listdir(temp_dir):
                    if f.endswith(('.mp3', '.m4a', '.wav')):
                        audio_file = os.path.join(temp_dir, f)
                        break

            if os.path.exists(audio_file):
                logger.info(f"[INSTAGRAM] Скачано: {audio_file}")
                return audio_file, metadata

    except Exception as e:
        logger.error(f"[INSTAGRAM] Ошибка: {e}")
        raise Exception(f"Не удалось скачать Instagram Reel. Добавь INSTAGRAM_COOKIES в Railway. Ошибка: {e}")
    finally:
        # Удаляем временный файл cookies
        if cookies_file and os.path.exists(cookies_file):
            os.unlink(cookies_file)


def download_video(url: str) -> tuple[str, dict]:
    """
    Скачивает видео и возвращает путь к файлу и метаданные.

    Returns:
        tuple: (путь_к_файлу, метаданные)
    """
    platform = detect_platform(url)
    logger.info(f"[VIDEO] Скачивание с {platform}: {url}")

    # Instagram обрабатываем отдельно
    if platform == 'instagram':
        return download_instagram_reel(url)

    # Создаём временную директорию
    temp_dir = tempfile.mkdtemp()
    output_template = os.path.join(temp_dir, '%(id)s.%(ext)s')

    ydl_opts = {
        'format': 'bestaudio/best',  # Для транскрипции нужен только аудио
        'outtmpl': output_template,
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '128',
        }],
    }

    # Для Instagram нужны cookies или другие методы
    if platform == 'instagram':
        ydl_opts['cookiefile'] = os.getenv('INSTAGRAM_COOKIES_FILE', '')

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

            # Собираем метаданные
            metadata = {
                'platform': platform,
                'video_id': info.get('id', ''),
                'title': info.get('title', ''),
                'description': info.get('description', ''),
                'duration': info.get('duration', 0),
                'view_count': info.get('view_count', 0),
                'like_count': info.get('like_count', 0),
                'comment_count': info.get('comment_count', 0),
                'uploader': info.get('uploader', ''),
                'upload_date': info.get('upload_date', ''),
                'url': url,
            }

            # Находим скачанный файл
            audio_file = os.path.join(temp_dir, f"{info['id']}.mp3")

            if not os.path.exists(audio_file):
                # Пробуем найти любой аудио файл
                for f in os.listdir(temp_dir):
                    if f.endswith(('.mp3', '.m4a', '.wav')):
                        audio_file = os.path.join(temp_dir, f)
                        break

            logger.info(f"[VIDEO] Скачано: {audio_file}")
            return audio_file, metadata

    except Exception as e:
        logger.error(f"[VIDEO] Ошибка скачивания: {e}")
        raise


def transcribe_audio(audio_path: str) -> str:
    """
    Транскрибирует аудио через OpenAI Whisper.

    Args:
        audio_path: Путь к аудио файлу

    Returns:
        str: Транскрипция
    """
    logger.info(f"[WHISPER] Транскрипция: {audio_path}")

    try:
        with open(audio_path, 'rb') as audio_file:
            response = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                language="ru",  # Можно сделать автоопределение
                response_format="text"
            )

        logger.info(f"[WHISPER] Транскрипция готова: {len(response)} символов")
        return response

    except Exception as e:
        logger.error(f"[WHISPER] Ошибка транскрипции: {e}")
        raise


def analyze_video(url: str) -> dict:
    """
    Полный анализ видео: скачивание + транскрипция + метаданные.

    Args:
        url: URL видео (Reels, Shorts, TikTok)

    Returns:
        dict: {
            'platform': 'youtube',
            'url': '...',
            'transcript': '...',
            'duration': 60,
            'view_count': 10000,
            ...
        }
    """
    audio_path = None

    try:
        # Скачиваем видео
        audio_path, metadata = download_video(url)

        # Транскрибируем
        transcript = transcribe_audio(audio_path)

        # Объединяем результат
        result = {
            **metadata,
            'transcript': transcript,
        }

        return result

    finally:
        # Удаляем временные файлы
        if audio_path and os.path.exists(audio_path):
            try:
                os.remove(audio_path)
                os.rmdir(os.path.dirname(audio_path))
            except:
                pass


def get_video_info_only(url: str) -> dict:
    """
    Получает только метаданные видео без скачивания.
    Быстрее, но без транскрипции.
    """
    platform = detect_platform(url)
    logger.info(f"[VIDEO] Получение инфо с {platform}: {url}")

    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

            return {
                'platform': platform,
                'video_id': info.get('id', ''),
                'title': info.get('title', ''),
                'description': info.get('description', ''),
                'duration': info.get('duration', 0),
                'view_count': info.get('view_count', 0),
                'like_count': info.get('like_count', 0),
                'comment_count': info.get('comment_count', 0),
                'uploader': info.get('uploader', ''),
                'upload_date': info.get('upload_date', ''),
                'url': url,
            }

    except Exception as e:
        logger.error(f"[VIDEO] Ошибка получения инфо: {e}")
        raise


# Тест
if __name__ == "__main__":
    test_url = "https://www.youtube.com/shorts/dQw4w9WgXcQ"
    result = analyze_video(test_url)
    print(f"Platform: {result['platform']}")
    print(f"Title: {result['title']}")
    print(f"Views: {result['view_count']}")
    print(f"Transcript: {result['transcript'][:200]}...")
