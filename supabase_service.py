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

    def _format_views_with_period(self, view_count: int, upload_date: str) -> str:
        """Форматирует просмотры с периодом: '126K/2мес'"""
        if not view_count:
            return ""

        # Форматируем число просмотров
        if view_count >= 1_000_000:
            views_str = f"{view_count / 1_000_000:.1f}M"
        elif view_count >= 1_000:
            views_str = f"{view_count / 1_000:.0f}K"
        else:
            views_str = str(view_count)

        # Если есть дата — добавляем период
        if upload_date:
            try:
                from datetime import datetime, date
                pub_date = datetime.strptime(upload_date, "%Y-%m-%d").date()
                today = date.today()
                days = (today - pub_date).days

                if days < 1:
                    period = "сегодня"
                elif days == 1:
                    period = "1д"
                elif days < 7:
                    period = f"{days}д"
                elif days < 30:
                    weeks = days // 7
                    period = f"{weeks}нед"
                elif days < 365:
                    months = days // 30
                    period = f"{months}мес"
                else:
                    years = days // 365
                    period = f"{years}г"

                return f"{views_str}/{period}"
            except:
                pass

        return views_str

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
            # Парсим дату публикации (формат YYYYMMDD от yt-dlp)
            upload_date = None
            upload_date_raw = video_info.get('upload_date', '')
            if upload_date_raw and len(upload_date_raw) == 8:
                try:
                    upload_date = f"{upload_date_raw[:4]}-{upload_date_raw[4:6]}-{upload_date_raw[6:8]}"
                except:
                    pass

            # Форматируем просмотры с периодом: "126K/2мес"
            view_count = video_info.get('view_count', 0) or 0
            views_str = self._format_views_with_period(view_count, upload_date)

            # Подготавливаем данные для вставки
            data = {
                "client_id": client_id,
                "link": video_info.get('url', ''),
                "platform": video_info.get('platform', ''),
                "video_id": video_info.get('video_id', ''),
                "views": views_str,
                "view_count": view_count,
                "upload_date": upload_date,
                "transcript": video_info.get('transcript', ''),
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
            # Получаем upload_date для форматирования
            response = self.client.table("video_analysis").select("upload_date").eq("id", video_id).single().execute()
            upload_date = response.data.get('upload_date') if response.data else None

            # Форматируем с периодом
            views_str = self._format_views_with_period(view_count, upload_date)

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


    # ─── Статусы роликов ──────────────────────────────────────────────────

    VALID_STATUSES = ['idea', 'facts', 'script', 'filmed', 'edited', 'published']

    def get_all_clients_status(self, month: str = None) -> list:
        """
        Для каждого клиента: имя, ниша, кол-во роликов по каждому статусу.
        month: '2026-04' или None = текущий.
        """
        if not month:
            from datetime import date
            month = date.today().strftime('%Y-%m')

        try:
            clients = self.get_clients()
            result = []

            for c in clients:
                topics_res = (
                    self.client.table("content_topics")
                    .select("id, sort_order, title, production_status")
                    .eq("client_id", c['id'])
                    .like("publish_date", f"{month}%")
                    .order("sort_order")
                    .execute()
                )
                topics = topics_res.data or []
                total = len(topics)

                # Считаем сколько роликов НА ЭТОМ ЭТАПЕ ИЛИ ВЫШЕ
                status_counts = {}
                for s in self.VALID_STATUSES:
                    idx = self.VALID_STATUSES.index(s)
                    status_counts[s] = sum(
                        1 for t in topics
                        if t.get('production_status') and
                        self.VALID_STATUSES.index(t['production_status']) >= idx
                    )

                published = status_counts.get('published', 0)

                result.append({
                    'id': c['id'],
                    'name': c['name'],
                    'niche': c.get('niche', ''),
                    'total': total,
                    'published': published,
                    'counts': status_counts,
                    'topics': topics,
                })

            return result

        except Exception as e:
            logger.error(f"[SUPABASE] Ошибка получения статусов: {e}")
            return []

    def get_client_topics(self, client_id: str, month: str = None) -> list:
        """Список тем клиента с текущим статусом."""
        if not month:
            from datetime import date
            month = date.today().strftime('%Y-%m')

        try:
            response = (
                self.client.table("content_topics")
                .select("id, sort_order, title, production_status")
                .eq("client_id", client_id)
                .like("publish_date", f"{month}%")
                .order("sort_order")
                .execute()
            )
            return response.data or []
        except Exception as e:
            logger.error(f"[SUPABASE] Ошибка получения тем: {e}")
            return []

    def update_topic_status(self, topic_id: str, new_status: str) -> bool:
        """Обновить production_status."""
        if new_status not in self.VALID_STATUSES:
            logger.error(f"[SUPABASE] Недопустимый статус: {new_status}")
            return False
        try:
            self.client.table("content_topics").update(
                {"production_status": new_status}
            ).eq("id", topic_id).execute()
            logger.info(f"[SUPABASE] Статус обновлён: {topic_id} → {new_status}")
            return True
        except Exception as e:
            logger.error(f"[SUPABASE] Ошибка обновления статуса: {e}")
            return False

    def update_topic_link(self, topic_id: str, link: str) -> bool:
        """Сохранить ссылку и поставить статус published."""
        try:
            self.client.table("content_topics").update({
                "material_link": link,
                "production_status": "published",
            }).eq("id", topic_id).execute()
            logger.info(f"[SUPABASE] Ссылка + published: {topic_id}")
            return True
        except Exception as e:
            logger.error(f"[SUPABASE] Ошибка обновления ссылки: {e}")
            return False

    def find_topic_by_number(self, client_id: str, number: int, month: str = None) -> dict:
        """Найти тему по порядковому номеру (1-based) в текущем месяце."""
        topics = self.get_client_topics(client_id, month)
        if 1 <= number <= len(topics):
            return topics[number - 1]
        return None

    def find_client_by_name(self, name_part: str) -> dict:
        """Нечёткий поиск клиента по части имени."""
        try:
            clients = self.get_clients()
            name_lower = name_part.lower().strip()
            for c in clients:
                if name_lower in c['name'].lower():
                    return c
            return None
        except Exception as e:
            logger.error(f"[SUPABASE] Ошибка поиска клиента: {e}")
            return None


# Тест
if __name__ == "__main__":
    manager = SupabaseManager()

    # Тест получения клиентов
    clients = manager.get_clients()
    print("Клиенты:")
    for c in clients:
        print(f"  - {c['name']} ({c['id']})")
