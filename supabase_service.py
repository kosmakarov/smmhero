"""
Сервис для работы с Supabase (Laikai) вместо Google Sheets.
Сохраняет анализ видео в таблицу video_analysis.
"""
import os
import logging
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


class SupabaseManager:
    """Менеджер для работы с Supabase."""

    def __init__(self):
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_ANON_KEY")

        if not url or not key:
            raise ValueError("SUPABASE_URL и SUPABASE_ANON_KEY должны быть установлены в .env")

        self.client: Client = create_client(url, key)
        logger.info("[SUPABASE] Подключено")

    def get_clients(self) -> list:
        """Получает список всех клиентов."""
        try:
            response = self.client.table("clients").select("id, name").order("name").execute()
            return response.data or []
        except Exception as e:
            logger.error(f"[SUPABASE] Ошибка получения клиентов: {e}")
            return []

    def get_client_by_id(self, client_id: str) -> dict:
        """Получает клиента по ID."""
        try:
            response = self.client.table("clients").select("*").eq("id", client_id).single().execute()
            return response.data
        except Exception as e:
            logger.error(f"[SUPABASE] Ошибка получения клиента: {e}")
            return None

    def add_video_analysis(self, client_id: str, video_info: dict, analysis: dict) -> dict:
        """
        Добавляет анализ видео в таблицу video_analysis.

        Args:
            client_id: UUID клиента
            video_info: Метаданные видео (url, platform, views и т.д.)
            analysis: Результат анализа GPT

        Returns:
            dict: Созданная запись или None при ошибке
        """
        try:
            # Форматируем просмотры
            views = video_info.get('view_count', 0)
            if views:
                if views >= 1_000_000:
                    views_str = f"{views / 1_000_000:.1f}M"
                elif views >= 1_000:
                    views_str = f"{views / 1_000:.0f}K"
                else:
                    views_str = str(views)
            else:
                views_str = ""

            # Подготавливаем данные для вставки
            data = {
                "client_id": client_id,
                "link": video_info.get('url', ''),
                "platform": video_info.get('platform', ''),
                "views": views_str,
                "hook": analysis.get('hook', ''),
                "visp": f"{analysis.get('visp', '')}\n{analysis.get('visp_details', '')}".strip(),
                "problem": analysis.get('problem', ''),
                "hunt_level": str(analysis.get('hunt_level', '')),
                "retention": analysis.get('retention', ''),
                "cta": analysis.get('cta', ''),
            }

            response = self.client.table("video_analysis").insert(data).execute()

            if response.data:
                logger.info(f"[SUPABASE] Добавлен анализ видео для клиента {client_id}")
                return response.data[0]
            return None

        except Exception as e:
            logger.error(f"[SUPABASE] Ошибка добавления анализа: {e}")
            return None

    def get_video_analyses(self, client_id: str, limit: int = 10) -> list:
        """Получает последние анализы для клиента."""
        try:
            response = (
                self.client.table("video_analysis")
                .select("*")
                .eq("client_id", client_id)
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
            return response.data or []
        except Exception as e:
            logger.error(f"[SUPABASE] Ошибка получения анализов: {e}")
            return []

    def add_idea(self, client_id: str, idea: str, note: str = "", link: str = "") -> dict:
        """Добавляет идею в банк идей клиента."""
        try:
            data = {
                "client_id": client_id,
                "idea": idea,
                "note": note or None,
                "link": link or None,
                "is_used": False,
            }

            response = self.client.table("ideas").insert(data).execute()

            if response.data:
                logger.info(f"[SUPABASE] Добавлена идея для клиента {client_id}")
                return response.data[0]
            return None

        except Exception as e:
            logger.error(f"[SUPABASE] Ошибка добавления идеи: {e}")
            return None


# Тест
if __name__ == "__main__":
    manager = SupabaseManager()

    # Тест получения клиентов
    clients = manager.get_clients()
    print("Клиенты:")
    for c in clients:
        print(f"  - {c['name']} ({c['id']})")
