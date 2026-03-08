"""
Модуль для анализа контента через GPT.
Заполняет поля таблицы насмотренности.
"""
import os
import json
import logging
from openai import OpenAI
from dotenv import load_dotenv
from knowledge_base import get_knowledge_base

load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Модель для анализа
MODEL = "gpt-4o"


def analyze_content(
    transcript: str,
    video_info: dict,
    niche: str = "",
    memory_examples: list = None
) -> dict:
    """
    Анализирует контент видео и возвращает структурированные данные
    для таблицы насмотренности.

    Args:
        transcript: Транскрипция видео
        video_info: Метаданные видео (просмотры, лайки и т.д.)
        niche: Ниша контента
        memory_examples: Примеры предыдущих анализов для обучения

    Returns:
        dict: Структурированный анализ для таблицы
    """
    logger.info("[GPT] Анализ контента...")

    # Получаем контекст из базы знаний
    kb = get_knowledge_base()
    knowledge_context = kb.get_context_for_analysis()

    if knowledge_context:
        logger.info(f"[GPT] Используем базу знаний: {kb.get_stats()}")

    # Формируем примеры из памяти
    examples_text = ""
    if memory_examples:
        examples_text = "\n\nПРИМЕРЫ ПРЕДЫДУЩИХ АНАЛИЗОВ (учитывай стиль):\n"
        for ex in memory_examples[-5:]:  # Последние 5 примеров
            examples_text += f"""
---
Транскрипт: {ex.get('transcript', '')[:200]}...
Тема: {ex.get('topic', '')}
ВИСП: {ex.get('visp', '')}
Хук: {ex.get('hook', '')}
---"""

    # Базовый промпт + знания из курса
    knowledge_section = ""
    if knowledge_context:
        knowledge_section = f"""
{knowledge_context}

---
ИСПОЛЬЗУЙ ЗНАНИЯ ВЫШЕ ПРИ АНАЛИЗЕ! Применяй концепции и фреймворки из базы знаний.
---

"""

    prompt = f"""Ты — эксперт по анализу вирусного контента в социальных сетях.
{knowledge_section}
Проанализируй видео и заполни таблицу насмотренности.

НИША: {niche or 'Не указана'}

МЕТАДАННЫЕ ВИДЕО:
- Платформа: {video_info.get('platform', 'unknown')}
- Просмотры: {video_info.get('view_count', 'N/A')}
- Лайки: {video_info.get('like_count', 'N/A')}
- Длительность: {video_info.get('duration', 'N/A')} сек
- Автор: {video_info.get('uploader', 'N/A')}

ТРАНСКРИПЦИЯ:
{transcript}
{examples_text}

Проанализируй и верни JSON:
{{
    "topic": "Тема видео (2-5 слов)",
    "format": "Формат: гг (говорящая голова), закадр, скетч, сторителлинг, лайфхак, и т.д.",
    "hook": "Первые 3 секунды / Опенинг — что цепляет внимание (дословно из транскрипта)",
    "visp": "ВИСП — Почему залетело? Укажи один или несколько: причастность (к чему?), интрига (какая?), страх, польза, эмоция, тренд",
    "visp_details": "Подробности ВИСП — раскрой почему именно это зацепило",
    "problem": "Какую проблему решает или нет проблемы",
    "hunt_level": "Уровень Ханта: 1-безразличие, 2-осознание проблемы, 3-поиск решения, 4-выбор продукта, 5-покупка",
    "retention": "Что держит до конца: техника присоска (какая?), знакомая ситуация, история, открытая петля, и т.д.",
    "cta": "CTA — есть ли призыв к действию, если да то какой: подписка, комментарий, сохранение, переход и т.д.",
    "idea_for_adaptation": "Как можно адаптировать эту идею для другой ниши (1-2 предложения)"
}}

Важно:
- Будь конкретным, не используй общие фразы
- ВИСП должен быть привязан к конкретным словам/моментам из транскрипта
- Хук — это ДОСЛОВНАЯ цитата первых секунд"""

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "Ты эксперт по вирусному контенту. Отвечаешь только валидным JSON."
                },
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.3
        )

        result = json.loads(response.choices[0].message.content)
        logger.info(f"[GPT] Анализ завершён: {result.get('topic', 'N/A')}")
        return result

    except Exception as e:
        logger.error(f"[GPT] Ошибка анализа: {e}")
        raise


def format_views(view_count) -> str:
    """Форматирует количество просмотров."""
    if not view_count:
        return "N/A"

    view_count = int(view_count)  # на случай float
    if view_count >= 1_000_000:
        return f"{view_count / 1_000_000:.1f} млн"
    elif view_count >= 1_000:
        return f"{view_count / 1_000:.0f}K"
    else:
        return str(view_count)


def format_duration(seconds) -> str:
    """Форматирует длительность."""
    if not seconds:
        return "N/A"

    seconds = int(seconds)  # yt-dlp может вернуть float
    minutes = seconds // 60
    secs = seconds % 60

    if minutes > 0:
        return f"{minutes}:{secs:02d}"
    else:
        return f"{secs} сек"


def format_days_since_upload(upload_date: str) -> str:
    """
    Вычисляет сколько дней прошло с публикации.

    Args:
        upload_date: Дата в формате YYYYMMDD (от yt-dlp)

    Returns:
        str: "3д" или "2нед" или "1мес" и т.д.
    """
    if not upload_date:
        return "N/A"

    from datetime import datetime

    try:
        # Парсим дату (формат YYYYMMDD)
        if len(upload_date) == 8:
            pub_date = datetime.strptime(upload_date, "%Y%m%d")
        else:
            # Может быть в другом формате
            pub_date = datetime.strptime(upload_date[:10], "%Y-%m-%d")

        days = (datetime.now() - pub_date).days

        if days == 0:
            return "сегодня"
        elif days == 1:
            return "1д"
        elif days < 7:
            return f"{days}д"
        elif days < 30:
            weeks = days // 7
            return f"{weeks}нед"
        elif days < 365:
            months = days // 30
            return f"{months}мес"
        else:
            years = days // 365
            return f"{years}г"

    except Exception:
        return "N/A"


def prepare_row_for_sheet(video_info: dict, analysis: dict) -> list:
    """
    Подготавливает строку для вставки в Google Sheets.
    Формат соответствует таблице насмотренности.

    Колонки:
    A: № (пустой, заполнится автоматически)
    B: Тема шортса
    C: Ссылка на шортс
    D: Формат
    E: Просмотры/время
    F: Первые 3 секунды/Опенинг
    G: ВИСП (почему залетел?)
    H: Проблема
    I: Уровень Ханта
    J: Что держит до конца
    K: CTA - есть ли, если да, то какие?
    """
    # Просмотры / время с публикации (не длительность видео!)
    views = format_views(video_info.get('view_count', 0))
    time_since = format_days_since_upload(video_info.get('upload_date', ''))
    views_time = f"{views} / {time_since}"

    return [
        "",  # № — заполнится формулой или вручную
        analysis.get('topic', ''),
        video_info.get('url', ''),
        analysis.get('format', ''),
        views_time,
        analysis.get('hook', ''),
        f"{analysis.get('visp', '')}\n{analysis.get('visp_details', '')}",
        analysis.get('problem', ''),
        analysis.get('hunt_level', ''),
        analysis.get('retention', ''),
        analysis.get('cta', ''),
    ]


def generate_ideas_from_content(analyses: list, target_niche: str) -> list:
    """
    Генерирует идеи для контента на основе проанализированных видео.

    Args:
        analyses: Список проанализированных видео
        target_niche: Ниша для которой генерируем идеи

    Returns:
        list: Список идей с адаптациями
    """
    logger.info(f"[GPT] Генерация идей для ниши: {target_niche}")

    # Собираем топ видео
    top_content = "\n\n".join([
        f"Тема: {a.get('topic', '')}\n"
        f"ВИСП: {a.get('visp', '')}\n"
        f"Хук: {a.get('hook', '')}\n"
        f"Идея адаптации: {a.get('idea_for_adaptation', '')}"
        for a in analyses[:10]
    ])

    prompt = f"""На основе успешных видео из разных ниш, предложи 5 идей для контента в нише "{target_niche}".

УСПЕШНЫЕ ВИДЕО:
{top_content}

Для каждой идеи укажи:
1. Тема/заголовок
2. Хук (первые слова)
3. Структура (кратко)
4. Почему должно залететь (ВИСП)

Верни JSON:
{{
    "ideas": [
        {{
            "title": "Тема видео",
            "hook": "Первые слова видео",
            "structure": "Краткая структура",
            "visp": "Почему залетит"
        }}
    ]
}}"""

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "Ты креативный директор контент-агентства."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.7
        )

        result = json.loads(response.choices[0].message.content)
        logger.info(f"[GPT] Сгенерировано {len(result.get('ideas', []))} идей")
        return result.get('ideas', [])

    except Exception as e:
        logger.error(f"[GPT] Ошибка генерации идей: {e}")
        raise


# Тест
if __name__ == "__main__":
    test_transcript = """
    Когда я начинала курить, мягкая была пачка,
    и сигарету можно было вытащить из кармана,
    не доставая пачку. А Мальборо изобрел пачку...
    """

    test_info = {
        'platform': 'youtube',
        'view_count': 7000000,
        'duration': 45,
        'uploader': 'test_channel'
    }

    result = analyze_content(test_transcript, test_info, niche="Маркетинг")
    print(json.dumps(result, ensure_ascii=False, indent=2))
