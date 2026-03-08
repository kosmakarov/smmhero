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
            # Форматируем просмотры для отображения
            view_count = video_info.get('view_count', 0) or 0
            if view_count:
                if view_count >= 1_000_000:
                    views_str = f"{view_count / 1_000_000:.1f}M"
                elif view_count >= 1_000:
                    views_str = f"{view_count / 1_000:.0f}K"
                else:
                    views_str = str(view_count)
            else:
                views_str = ""

            # Парсим дату публикации (формат YYYYMMDD от yt-dlp)
            upload_date = None
            upload_date_raw = video_info.get('upload_date', '')
            if upload_date_raw and len(upload_date_raw) == 8:
                try:
                    upload_date = f"{upload_date_raw[:4]}-{upload_date_raw[4:6]}-{upload_date_raw[6:8]}"
                except:
                    pass

            # Подготавливаем данные для вставки
            data = {
                "client_id": client_id,
                "link": video_info.get('url', ''),
                "platform": video_info.get('platform', ''),
                "video_id": video_info.get('video_id', ''),
                "views": views_str,
                "view_count": view_count,
                "upload_date": upload_date,
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

                # Добавляем первую запись в историю просмотров
                if view_count > 0:
                    self.add_view_snapshot(response.data[0]['id'], view_count)

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

    def add_view_snapshot(self, video_analysis_id: str, view_count: int) -> dict:
        """Добавляет снимок просмотров в историю."""
        try:
            data = {
                "video_analysis_id": video_analysis_id,
                "view_count": view_count,
            }
            response = self.client.table("video_views_history").insert(data).execute()
            if response.data:
                logger.info(f"[SUPABASE] Добавлен снимок просмотров: {view_count}")
                return response.data[0]
            return None
        except Exception as e:
            logger.error(f"[SUPABASE] Ошибка добавления снимка: {e}")
            return None

    def get_all_videos_for_tracking(self) -> list:
        """Получает все видео для трекинга (с ссылками)."""
        try:
            response = (
                self.client.table("video_analysis")
                .select("id, client_id, link, platform, view_count, upload_date")
                .not_.is_("link", "null")
                .execute()
            )
            return response.data or []
        except Exception as e:
            logger.error(f"[SUPABASE] Ошибка получения видео: {e}")
            return []

    def update_video_views(self, video_id: str, view_count: int) -> bool:
        """Обновляет просмотры видео и добавляет в историю."""
        try:
            # Форматируем для отображения
            if view_count >= 1_000_000:
                views_str = f"{view_count / 1_000_000:.1f}M"
            elif view_count >= 1_000:
                views_str = f"{view_count / 1_000:.0f}K"
            else:
                views_str = str(view_count)

            # Обновляем текущие просмотры
            self.client.table("video_analysis").update({
                "view_count": view_count,
                "views": views_str,
            }).eq("id", video_id).execute()

            # Добавляем в историю
            self.add_view_snapshot(video_id, view_count)

            logger.info(f"[SUPABASE] Обновлены просмотры: {video_id} -> {views_str}")
            return True
        except Exception as e:
            logger.error(f"[SUPABASE] Ошибка обновления просмотров: {e}")
            return False

    def get_views_history(self, video_analysis_id: str) -> list:
        """Получает историю просмотров видео."""
        try:
            response = (
                self.client.table("video_views_history")
                .select("view_count, recorded_at")
                .eq("video_analysis_id", video_analysis_id)
                .order("recorded_at")
                .execute()
            )
            return response.data or []
        except Exception as e:
            logger.error(f"[SUPABASE] Ошибка получения истории: {e}")
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
