"""Бесплатный тизер пары для VK-бота: три строки и картинка.

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

# Луна за сутки проходит около 13°: при неизвестном времени она могла быть на
# 6,5° в любую сторону от полуденной. Ближе к границе знака — знак не называем.
MOON_SIGN_MARGIN = 6.5

LABEL_STRONG = "Сильное"
LABEL_SPARK = "Где искрит"
LABEL_MORE = "Что видно только в полном разборе"

_PERSONAL = ("Sun", "Moon", "Venus", "Mars")


class TeaserError(RuntimeError):
    """Тизер не получился: модель не ответила или ответ не разобрался."""


class TeaserBudgetError(TeaserError):
    """Дневной потолок генераций выбран — тизер сегодня не считаем."""


@dataclass(frozen=True, slots=True)
class Teaser:
    strong: str
    spark: str
    more: str
    # None — картинку нарисовать не вышло; тизер тогда уходит текстом.
    image: bytes | None

    @property
    def text(self) -> str:
        return (
            f"{LABEL_STRONG}: {self.strong}\n\n"
            f"{LABEL_SPARK}: {self.spark}\n\n"
            f"{LABEL_MORE}: {self.more}"
        )


# --- Отбор фактов ------------------------------------------------------------


def _moon_sign(natal: dict[str, Any], *, time_known: bool) -> str | None:
    """Знак Луны; при неизвестном времени — только вдали от границы знака."""
    moon = next(p for p in natal["planets"] if p["name"] == "Moon")
    if not time_known:
        deg = moon["deg_in_sign"]
        if deg < MOON_SIGN_MARGIN or deg > 30 - MOON_SIGN_MARGIN:
            return None
    return moon["sign_ru"]


def _signs(natal: dict[str, Any], *, time_known: bool) -> dict[str, str]:
    out = {p["name"]: p["sign_ru"] for p in natal["planets"] if p["name"] in ("Sun", "Venus", "Mars")}
    moon = _moon_sign(natal, time_known=time_known)
    if moon is not None:
        out["Moon"] = moon
    return out


def facts(
    calc: dict[str, Any],
    natal_a: dict[str, Any],
    natal_b: dict[str, Any],
) -> dict[str, Any]:
    """Что из синастрии уходит в промпт тизера.

    Берём из полного списка calc["aspects"], а не из верхушки: Венера–Марс
    туда попадает не всегда. Контакты Луны стороны без времени выбрасываем —
    её положение приблизительно, от неё остаётся только знак.
    """
    known = {"a": calc["meta"]["a"]["time_known"], "b": calc["meta"]["b"]["time_known"]}

    def usable(aspect: dict[str, Any]) -> bool:
        return not (
            (aspect["a"] == "Moon" and not known["a"]) or (aspect["b"] == "Moon" and not known["b"])
        )

    aspects = [a for a in calc["aspects"] if usable(a)]

    def between(first: str, second: str) -> list[dict[str, Any]]:
        return [a for a in aspects if {a["a"], a["b"]} == {first, second}]

    saturn = [
        a for a in aspects
        if (a["a"] == "Saturn" and a["b"] in _PERSONAL) or (a["b"] == "Saturn" and a["a"] in _PERSONAL)
    ]

    summary = calc["summary"]
    rest = []
    if any("Mercury" in (a["a"], a["b"]) for a in aspects):
        rest.append("как вы разговариваете и слышите друг друга (контакты Меркурия)")
    if summary["reciprocal"]:
        rest.append("контакты, которые работают в обе стороны")
    if summary["voids"]["a"] or summary["voids"]["b"]:
        rest.append("планеты, которые в этой паре молчат")
    rest.append("как вы ссоритесь и миритесь")
    rest.append("точка роста пары")

    return {
        "signs": {
            "a": _signs(natal_a, time_known=known["a"]),
            "b": _signs(natal_b, time_known=known["b"]),
        },
        "venus_mars": between("Venus", "Mars"),
        "sun_moon": between("Sun", "Moon"),
        "saturn": saturn,
        "rest": rest,
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
    selected = facts(calc, chart.calc["natal"], pair_data["natal"])
    user_prompt = prompt.build_user(selected, name_b=pair_data.get("name"))

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

    image: bytes | None
    try:
        # Pillow рисует сотню миллисекунд — в поток, чтобы не держать цикл событий.
        image = await asyncio.to_thread(
            card.render_pair,
            selected["signs"]["a"]["Sun"],
            selected["signs"]["b"]["Sun"],
            lines["strong"],
            lines["spark"],
        )
    except Exception as e:  # noqa: BLE001 — картинка не важнее текста тизера
        logger.error("Тизер пары: картинка не нарисовалась, карта %s: %s", chart.id, type(e).__name__)
        image = None

    return Teaser(strong=lines["strong"], spark=lines["spark"], more=lines["more"], image=image)
