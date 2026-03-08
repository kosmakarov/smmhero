"""
База знаний Вуди — хранит и использует знания из обучающих материалов.
"""
import os
import json
import logging
from pathlib import Path
from typing import Optional

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Папка с файлами знаний
KNOWLEDGE_DIR = Path(__file__).parent / "knowledge"


class KnowledgeBase:
    """Управляет базой знаний агента."""

    def __init__(self):
        self.lessons: list[dict] = []
        self.concepts: dict = {}  # name -> concept
        self.frameworks: list[dict] = []
        self.tips: list[str] = []
        self.analysis_criteria: dict = {}

        self._load_all()

    def _load_all(self):
        """Загружает все файлы знаний."""
        if not KNOWLEDGE_DIR.exists():
            KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
            logger.info(f"[KNOWLEDGE] Создана папка: {KNOWLEDGE_DIR}")
            return

        json_files = list(KNOWLEDGE_DIR.glob("*.json"))
        logger.info(f"[KNOWLEDGE] Найдено файлов: {len(json_files)}")

        for file_path in sorted(json_files):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                self.lessons.append({
                    'file': file_path.name,
                    'title': data.get('lesson_title', file_path.stem),
                    'data': data
                })

                # Извлекаем концепции
                for concept in data.get('key_concepts', []):
                    name = concept.get('name', '').lower()
                    if name:
                        self.concepts[name] = concept

                # Извлекаем фреймворки
                self.frameworks.extend(data.get('frameworks', []))

                # Извлекаем советы
                self.tips.extend(data.get('practical_tips', []))

                # Критерии анализа
                if data.get('analysis_criteria'):
                    for key, value in data['analysis_criteria'].items():
                        if key not in self.analysis_criteria:
                            self.analysis_criteria[key] = []
                        if isinstance(value, list):
                            self.analysis_criteria[key].extend(value)

                logger.info(f"[KNOWLEDGE] Загружен: {file_path.name}")

            except Exception as e:
                logger.error(f"[KNOWLEDGE] Ошибка загрузки {file_path}: {e}")

        logger.info(f"[KNOWLEDGE] Итого: {len(self.concepts)} концепций, {len(self.frameworks)} фреймворков")

    def reload(self):
        """Перезагружает базу знаний."""
        self.lessons = []
        self.concepts = {}
        self.frameworks = []
        self.tips = []
        self.analysis_criteria = {}
        self._load_all()

    def get_concept(self, name: str) -> Optional[dict]:
        """Получает концепцию по имени."""
        return self.concepts.get(name.lower())

    def get_all_concepts(self) -> list[dict]:
        """Возвращает все концепции."""
        return list(self.concepts.values())

    def get_context_for_analysis(self) -> str:
        """
        Формирует контекст из базы знаний для анализа видео.
        Это добавляется в промпт GPT.
        """
        if not self.concepts and not self.frameworks:
            return ""

        context_parts = []

        # Ключевые концепции
        if self.concepts:
            context_parts.append("## БАЗА ЗНАНИЙ — КЛЮЧЕВЫЕ КОНЦЕПЦИИ:\n")
            for name, concept in self.concepts.items():
                context_parts.append(f"### {concept.get('name', name).upper()}")
                context_parts.append(f"Определение: {concept.get('definition', 'N/A')}")

                if concept.get('types'):
                    context_parts.append(f"Типы: {', '.join(concept['types'])}")

                if concept.get('how_to_identify'):
                    context_parts.append(f"Как определить: {concept['how_to_identify']}")

                if concept.get('examples'):
                    context_parts.append(f"Примеры: {'; '.join(concept['examples'][:3])}")

                context_parts.append("")

        # Фреймворки
        if self.frameworks:
            context_parts.append("## ФРЕЙМВОРКИ ДЛЯ АНАЛИЗА:\n")
            for fw in self.frameworks[:5]:  # Максимум 5 фреймворков
                context_parts.append(f"**{fw.get('name', 'Фреймворк')}**")
                if fw.get('steps'):
                    for i, step in enumerate(fw['steps'], 1):
                        context_parts.append(f"  {i}. {step}")
                context_parts.append("")

        # Критерии анализа
        if self.analysis_criteria:
            context_parts.append("## КРИТЕРИИ ОЦЕНКИ:\n")

            if self.analysis_criteria.get('what_to_look_for'):
                context_parts.append("На что смотреть:")
                for item in self.analysis_criteria['what_to_look_for'][:5]:
                    context_parts.append(f"  - {item}")

            if self.analysis_criteria.get('green_flags'):
                context_parts.append("\nПризнаки хорошего контента:")
                for item in self.analysis_criteria['green_flags'][:5]:
                    context_parts.append(f"  + {item}")

            if self.analysis_criteria.get('red_flags'):
                context_parts.append("\nПризнаки плохого контента:")
                for item in self.analysis_criteria['red_flags'][:5]:
                    context_parts.append(f"  - {item}")

        return "\n".join(context_parts)

    def get_tips(self, limit: int = 10) -> list[str]:
        """Возвращает практические советы."""
        return self.tips[:limit]

    def search(self, query: str) -> list[dict]:
        """
        Ищет релевантные знания по запросу.
        Простой поиск по ключевым словам.
        """
        query_lower = query.lower()
        results = []

        # Ищем в концепциях
        for name, concept in self.concepts.items():
            if query_lower in name or query_lower in concept.get('definition', '').lower():
                results.append({
                    'type': 'concept',
                    'data': concept
                })

        # Ищем в фреймворках
        for fw in self.frameworks:
            if query_lower in fw.get('name', '').lower():
                results.append({
                    'type': 'framework',
                    'data': fw
                })

        return results

    def get_stats(self) -> dict:
        """Статистика базы знаний."""
        return {
            'lessons': len(self.lessons),
            'concepts': len(self.concepts),
            'frameworks': len(self.frameworks),
            'tips': len(self.tips),
        }


# Глобальный экземпляр
_knowledge_base: Optional[KnowledgeBase] = None


def get_knowledge_base() -> KnowledgeBase:
    """Получает или создаёт экземпляр базы знаний."""
    global _knowledge_base
    if _knowledge_base is None:
        _knowledge_base = KnowledgeBase()
    return _knowledge_base


def reload_knowledge():
    """Перезагружает базу знаний."""
    global _knowledge_base
    if _knowledge_base:
        _knowledge_base.reload()
    else:
        _knowledge_base = KnowledgeBase()


# Тест
if __name__ == "__main__":
    kb = get_knowledge_base()
    stats = kb.get_stats()
    print(f"Статистика: {stats}")

    context = kb.get_context_for_analysis()
    if context:
        print(f"\nКонтекст для анализа:\n{context[:1000]}...")
    else:
        print("\nБаза знаний пуста. Добавьте JSON файлы в папку knowledge/")
