"""
Единая точка транскрипции для Вуди.

Приоритет — ЛОКАЛЬНЫЙ faster-whisper на сервере (бесплатно, не зависит
от баланса OpenAI). Если локальный недоступен (например, на маке при
разработке) — откат на OpenAI Whisper API.

Локальный путь задаётся через переменные окружения (см. .env):
  LOCAL_WHISPER_PYTHON  — питон из whisper-venv
  LOCAL_WHISPER_SCRIPT  — путь к transcribe.py
"""
import os
import logging
import subprocess

logger = logging.getLogger(__name__)

LOCAL_PYTHON = os.getenv("LOCAL_WHISPER_PYTHON", "/opt/whisper-venv/bin/python3")
LOCAL_SCRIPT = os.getenv("LOCAL_WHISPER_SCRIPT", "/opt/whisper/transcribe.py")


def _local_available() -> bool:
    return os.path.exists(LOCAL_PYTHON) and os.path.exists(LOCAL_SCRIPT)


def transcribe(audio_path: str, language: str = "ru") -> str:
    """Транскрибирует аудиофайл. Сначала локально, при сбое — через OpenAI."""
    if _local_available():
        try:
            logger.info(f"[WHISPER] локально: {audio_path}")
            env = dict(os.environ, WHISPER_LANG=language)
            r = subprocess.run(
                [LOCAL_PYTHON, LOCAL_SCRIPT, audio_path],
                capture_output=True, text=True, timeout=900, env=env,
            )
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
            logger.warning(f"[WHISPER] локальный сбой rc={r.returncode}: {r.stderr[-200:]}")
        except Exception as e:
            logger.warning(f"[WHISPER] локальный упал: {e} — пробую OpenAI")

    # Fallback — OpenAI API
    logger.info(f"[WHISPER] OpenAI: {audio_path}")
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    with open(audio_path, "rb") as f:
        resp = client.audio.transcriptions.create(
            model="whisper-1", file=f, language=language,
        )
    return resp.text or ""
