"""Астропаспорт: единый JSON, который идёт и в LLM, и на фронт.

Чистый агрегатор поверх расчётных модулей: натал (natal.py), а позже —
Матрица Судьбы (matrix.py) и восточный гороскоп (eastern.py). Про FastAPI,
базу и VK ничего не знает: на вход — уже посчитанные секции и «паспортные»
данные человека, на выход — один dict.

Формат версионируется (PASSPORT_VERSION): когда состав секций меняется,
версия растёт — по ней потом инвалидируется кеш отчётов и золотые тесты
понимают, с какой раскладкой сравнивают.

Сейчас в проекте готов только натал, поэтому matrix/eastern приходят None и
кладутся в паспорт как null — место под них зарезервировано, промпт и фронт
уже могут рассчитывать на стабильные ключи.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

# Версия РАСКЛАДКИ паспорта (не промпта и не расчёта). Меняем при добавлении
# или переименовании секций верхнего уровня.
PASSPORT_VERSION = 1


@dataclass(frozen=True, slots=True)
class Person:
    """«Паспортные» данные, которые расчёт не даёт, но нужны разбору.

    Отделены от расчётной части: имя и пол влияют на тон текста (обращение),
    а birth_* — человекочитаемая шапка карты для промпта и фронта.
    Всё, кроме имени, опционально: анонимный разбор тоже возможен.
    """

    name: str | None = None
    sex: str | None = None  # "male" | "female" | None (обращение в тексте)
    birth_date: str | None = None  # "1990-05-11" — как ввёл пользователь
    birth_time: str | None = None  # "17:20"
    birth_place: str | None = None  # "Касимов (RU)" — подпись места


def build_passport(
    *,
    person: Person,
    natal: dict,
    matrix: dict | None = None,
    eastern: dict | None = None,
) -> dict:
    """Собирает астропаспорт из готовых секций.

    Именованные аргументы — намеренно: порядок секций не должен решаться
    позицией, а вызов на стороне склейки (services/charts.py, CLI) читается
    как перечисление того, что вошло в паспорт.
    """
    return {
        "passport_version": PASSPORT_VERSION,
        "person": asdict(person),
        "natal": natal,
        "matrix": matrix,  # заглушка до astro_core/matrix.py
        "eastern": eastern,  # заглушка до astro_core/eastern.py
    }
