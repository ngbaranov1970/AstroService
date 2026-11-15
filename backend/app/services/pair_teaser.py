"""Бесплатный тизер пары для VK-бота: короткий пост про пару и картинка.

Пункты пишет модель по контактам, которые выбрал facts(); заголовки разделов и фразу про платный разбор (QUESTION) ставит код.

Место в цепочке: chatbot/flows/pair.py (Job) → ЭТОТ МОДУЛЬ → vip_pair
(синастрия), llm (pair_teaser), card (PNG). В reports тизер не пишется и не
кешируется: адреса в кеше у него нет — партнёр живёт вне charts и pairs.
Траты держит дневной потолок: отчёты за сутки плюс показанные тизеры.
В лог идут только id и размеры (CLAUDE.md, «Персональных данных в логах нет»).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.llm import client as llm
from app.llm.prompts import pair_teaser as prompt
from app.models import Chart, Event
from app.services import card, events, reports, vip_pair

logger = logging.getLogger(__name__)

# Версия промпта — в заказ вместе с текстом тизера.
VERSION = prompt.VERSION

HEAD_SPARK = "🔥 Где искрит"
HEAD_STRONG = "💎 Что держит"
BULLET = "▪"
# Фразу про платный разбор ставит код, а не модель: тизер обещает только то,
# что разбор содержит всегда — причину и советы.
QUESTION = "Почему у вас так выходит и что с этим делать — в полном разборе пары."

_PERSONAL = ("Sun", "Moon", "Venus", "Mars")


class TeaserError(RuntimeError):
    """Тизер не получился: модель не ответила или ответ не разобрался."""


class TeaserBudgetError(TeaserError):
    """Дневной потолок генераций выбран — тизер сегодня не считаем."""


@dataclass(frozen=True, slots=True)
class Teaser:
    hook: str
    strong: tuple[str, ...]
    spark: tuple[str, ...]
    outcome: str
    # None — картинку нарисовать не вышло; тизер тогда уходит текстом.
    image: bytes | None
    # Контакты, по которым написан каждый пункт («Сатурн A — соединение — Солнце B»):
    # платному разбору они нужны, чтобы объяснить тизер тем же расчётом.
    strong_contacts: tuple[str, ...] = ()
    spark_contacts: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        def section(head: str, items: tuple[str, ...]) -> str:
            return "\n\n".join([head, *(f"{BULLET} {item}" for item in items)])

        return "\n\n".join(
            [
                # Заголовок начинаем с прописной сами: модель пишет его со строчной.
                self.hook[:1].upper() + self.hook[1:],
                section(HEAD_STRONG, self.strong),
                section(HEAD_SPARK, self.spark),
                f"Итог: {self.outcome}",
                QUESTION,
            ]
        )


# --- Отбор фактов ------------------------------------------------------------


_SOFT = ("trine", "sextile")
_HARD = ("square", "opposition")


def _is_strong(aspect: dict[str, Any]) -> bool:
    """Годится ли контакт в «Что держит»: трин, секстиль или соединение без Сатурна."""
    if aspect["type"] == "conjunction":
        return "Saturn" not in (aspect["a"], aspect["b"])
    return aspect["type"] in _SOFT


def _is_spark(aspect: dict[str, Any]) -> bool:
    """Годится ли контакт в «Где искрит»: квадрат, оппозиция или соединение с Сатурном."""
    if aspect["type"] == "conjunction":
        return "Saturn" in (aspect["a"], aspect["b"])
    return aspect["type"] in _HARD


def _contact(aspect: dict[str, Any]) -> str:
    """Контакт строкой, в том же виде, что в расчёте для платного разбора."""
    return f"{aspect['a_ru']} A — {aspect['type_ru']} — {aspect['b_ru']} B"


def facts(calc: dict[str, Any]) -> dict[str, Any]:
    """Что из синастрии уходит в промпт тизера: контакты на каждый пункт.

    Контакты выбирает код, а не модель: strong — самые весомые гармоничные
    контакты личных планет, spark — самые весомые напряжённые, до MAX_ITEMS в
    списке; пустой список — таких в паре нет. Луну стороны без времени не
    берём: за сутки она проходит около 13°, и контакт мог не сложиться.
    """
    known = {"a": calc["meta"]["a"]["time_known"], "b": calc["meta"]["b"]["time_known"]}

    def usable(aspect: dict[str, Any]) -> bool:
        return not (
            (aspect["a"] == "Moon" and not known["a"]) or (aspect["b"] == "Moon" and not known["b"])
        )

    def personal(aspect: dict[str, Any]) -> bool:
        # Обе планеты личные, либо Сатурн одного к личной планете другого.
        pair = (aspect["a"], aspect["b"])
        if all(name in _PERSONAL for name in pair):
            return True
        return "Saturn" in pair and any(name in _PERSONAL for name in pair)

    # calc["aspects"] отсортирован по весу — первые подходящие и есть сильнейшие.
    candidates = [a for a in calc["aspects"] if usable(a) and personal(a)]

    return {
        "strong": [a for a in candidates if _is_strong(a)][: prompt.MAX_ITEMS],
        "spark": [a for a in candidates if _is_spark(a)][: prompt.MAX_ITEMS],
    }


# --- Потолок -----------------------------------------------------------------


async def over_cap(session: AsyncSession) -> bool:
    """Выбран ли дневной потолок: отчёты за сутки плюс показанные тизеры."""
    since = datetime.now(UTC) - timedelta(days=1)
    teasers = await session.scalar(
        select(func.count()).where(
            Event.name == events.BOT_PAIR_TEASER_SHOWN, Event.ts >= since
        )
    )
    total = await reports.count_generated_last_day(session) + (teasers or 0)
    return total >= get_settings().daily_generation_limit


# --- Тизер -------------------------------------------------------------------


async def make(session: AsyncSession, chart: Chart, pair_data: dict[str, Any]) -> Teaser:
    """Тизер пары «клиент + партнёр из pair_data». Событие пишет вызывающий.

    Бросает TeaserBudgetError (потолок) и TeaserError (модель или её ответ).
    """
    if await over_cap(session):
        logger.warning("Тизер пары: дневной потолок выбран, карта %s", chart.id)
        raise TeaserBudgetError("Дневной потолок генераций выбран")

    calc = vip_pair.synastry(chart, pair_data)
    selected = facts(calc)
    name = (pair_data.get("name") or "").strip()
    user_prompt = prompt.build_user(selected, name_b=name)

    try:
        result = await llm.chat(
            prompt.SYSTEM,
            user_prompt,
            temperature=prompt.TEMPERATURE,
            max_tokens=prompt.MAX_TOKENS,
        )
    except llm.LLMError as e:
        logger.error("Тизер пары: %s не ответил, карта %s: %s", prompt.VERSION, chart.id, e)
        raise TeaserError("Модель не ответила") from e

    lines = prompt.parse(result.text)
    logger.info(
        "Тизер пары: %s, карта %s, промпт %d симв., ответ %d симв.",
        prompt.VERSION, chart.id, len(user_prompt), len(result.text),
    )
    if lines is None:
        raise TeaserError("Ответ модели не разобрался")
    # Пунктов не больше, чем контактов: лишний пункт модель пишет из головы.
    for key in ("strong", "spark"):
        lines[key] = lines[key][: max(1, len(selected[key]))]

    image: bytes | None
    try:
        # Pillow рисует сотню миллисекунд — в поток, чтобы не держать цикл событий.
        image = await asyncio.to_thread(
            card.render_pair,
            # Имён в заголовке картинки хватает: тизер считан по датам, а не по знакам.
            f"Ты и {name}" if name else "Ваша пара",
            # На картинку — первые пункты разделов: все шесть на 1080×1080 не лягут.
            lines["strong"][0],
            lines["spark"][0],
        )
    except Exception as e:  # noqa: BLE001 — картинка не важнее текста тизера
        logger.error("Тизер пары: картинка не нарисовалась, карта %s: %s", chart.id, type(e).__name__)
        image = None

    return Teaser(
        hook=lines["hook"],
        strong=tuple(lines["strong"]),
        spark=tuple(lines["spark"]),
        outcome=lines["outcome"],
        image=image,
        strong_contacts=tuple(_contact(a) for a in selected["strong"]),
        spark_contacts=tuple(_contact(a) for a in selected["spark"]),
    )
