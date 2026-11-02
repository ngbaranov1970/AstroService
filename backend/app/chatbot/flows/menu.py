"""Главное меню и выбор знака Солнца без карты.

Гороскоп на сегодня/месяц и подписка подключатся в задачах 5–7: здесь они
либо ведут в ask_sign (знака нет), либо отвечают заглушкой (знак есть).
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.chatbot import states, storage, texts
from app.chatbot.types import Button, Incoming, Reply
from app.llm.prompts.daily_horoscope import SIGN_ORDER
from app.models import BotDialog, User
from app.schemas.charts import MIN_BIRTH_YEAR
from app.services import sun_sign as sun_sign_service
from astro_core.transits import sun_sign_bounds, sun_sign_on

_DATE_RE = re.compile(
    r"^\s*(\d{1,2})[./](\d{1,2})[./](\d{2,4})\s*$"
)
_SIGN_BY_NORM = {s.lower().replace("ё", "е"): s for s in SIGN_ORDER}


def knows_sign(user: User) -> bool:
    """Знак известен: поле sun_sign или своя карта (рассылка берёт знак из неё)."""
    return bool(user.sun_sign or user.primary_chart_id)


def menu_reply(user: User) -> Reply:
    """Обычная клавиатура главного меню; подпись подписки зависит от флага."""
    sub_label = texts.BTN_UNSUBSCRIBE if user.subscribed else texts.BTN_SUBSCRIBE
    sub_cmd = "unsubscribe" if user.subscribed else "subscribe"
    return Reply(
        text=texts.MENU_TEXT,
        buttons=[
            [
                Button(texts.BTN_TODAY, {"cmd": "today"}),
                Button(texts.BTN_MONTH, {"cmd": "month"}),
            ],
            [
                Button(texts.BTN_NATAL, {"cmd": "natal"}),
                Button(texts.BTN_PAIR, {"cmd": "pair"}),
            ],
            [
                Button(texts.BTN_MY_SIGN, {"cmd": "sign"}),
                Button(sub_label, {"cmd": sub_cmd}),
            ],
        ],
    )


async def dispatch(
    session: AsyncSession,
    user: User,
    dialog: BotDialog,
    payload: dict[str, Any],
) -> list[Reply] | None:
    """Обработать cmd меню/знака. None — команда не из этого flow."""
    cmd = str(payload.get("cmd", ""))
    if cmd == "sign":
        return await start_ask_sign(session, user, dialog)
    if cmd == "set_sign":
        return await _set_sign(session, user, dialog, payload.get("sign"))
    if cmd == "sign_date":
        return await _ask_date(session, dialog, dialog.data)
    if cmd in {"today", "month"}:
        if not knows_sign(user):
            return await start_ask_sign(
                session, user, dialog, pending=cmd
            )
        # Задачи 5–6 отдадут текст; пока не уводим в чужой FSM.
        return [Reply(text=texts.STUB_HOROSCOPE), menu_reply(user)]
    if cmd in {"subscribe", "unsubscribe"}:
        # Задача 7: явный opt-in. Пока не меняем флаг.
        return [menu_reply(user)]
    return None


async def start_ask_sign(
    session: AsyncSession,
    user: User,
    dialog: BotDialog,
    *,
    pending: str | None = None,
) -> list[Reply]:
    """Открыть выбор знака: 12 кнопок + «Ввести дату». pending — today/month."""
    data: dict[str, Any] = {}
    if pending:
        data["pending"] = pending
    await storage.save(session, dialog, state=states.ASK_SIGN, data=data)
    if user.sun_sign:
        text = texts.ASK_SIGN_AGAIN.format(sign=user.sun_sign)
    else:
        text = texts.ASK_SIGN
    return [Reply(text=text, buttons=_sign_keyboard(), inline=True)]


async def handle_ask_sign(
    session: AsyncSession,
    user: User,
    dialog: BotDialog,
    incoming: Incoming,
) -> list[Reply]:
    """Состояние ask_sign: payload уже разобран роутером; сюда — текст."""
    data = dict(dialog.data or {})
    normalized = incoming.text.strip()

    sign = _SIGN_BY_NORM.get(normalized.lower().replace("ё", "е"))
    if sign is not None:
        return await _set_sign(session, user, dialog, sign)

    parsed = parse_birth_date(normalized)
    if parsed is None:
        return [Reply(text=texts.SIGN_BAD_DATE)]
    if not _date_in_range(parsed):
        return [Reply(text=texts.SIGN_DATE_RANGE)]
    return await _apply_birth_date(session, user, dialog, parsed, data)


def parse_birth_date(text: str) -> date | None:
    """Разобрать ДД.ММ.ГГГГ / ДД.ММ.ГГ; None — не дата."""
    match = _DATE_RE.match(text)
    if match is None:
        return None
    day_s, month_s, year_s = match.groups()
    year = int(year_s)
    if year < 100:
        # Двузначный год: 00–29 → 2000+, иначе 1900+ — как привычные формы.
        year += 2000 if year <= 29 else 1900
    try:
        return date(year, int(month_s), int(day_s))
    except ValueError:
        return None


def _sign_keyboard() -> list[list[Button]]:
    rows: list[list[Button]] = []
    row: list[Button] = []
    for sign in SIGN_ORDER:
        row.append(Button(sign, {"cmd": "set_sign", "sign": sign}))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([Button(texts.BTN_ENTER_DATE, {"cmd": "sign_date"})])
    return rows


async def _ask_date(
    session: AsyncSession,
    dialog: BotDialog,
    data: dict[str, Any],
) -> list[Reply]:
    next_data = {**data, "mode": "date"}
    await storage.save(session, dialog, state=states.ASK_SIGN, data=next_data)
    return [Reply(text=texts.ASK_SIGN_DATE)]


async def _apply_birth_date(
    session: AsyncSession,
    user: User,
    dialog: BotDialog,
    birth_date: date,
    data: dict[str, Any],
) -> list[Reply]:
    sign = sun_sign_on(birth_date)
    if sign is None:
        left, right = sun_sign_bounds(birth_date)
        cusp_data = {**data, "mode": "cusp"}
        await storage.save(session, dialog, state=states.ASK_SIGN, data=cusp_data)
        return [
            Reply(
                text=texts.ASK_SIGN_CUSP,
                buttons=[
                    [
                        Button(left, {"cmd": "set_sign", "sign": left}),
                        Button(right, {"cmd": "set_sign", "sign": right}),
                    ]
                ],
                inline=True,
            )
        ]
    await sun_sign_service.apply(session, user, sign)
    return await _after_sign_saved(session, user, dialog, sign, data)


async def _set_sign(
    session: AsyncSession,
    user: User,
    dialog: BotDialog,
    sign: Any,
) -> list[Reply]:
    if not isinstance(sign, str) or sign not in SIGN_ORDER:
        return await start_ask_sign(session, user, dialog)
    data = dict(dialog.data or {})
    await sun_sign_service.apply(session, user, sign)
    return await _after_sign_saved(session, user, dialog, sign, data)


async def _after_sign_saved(
    session: AsyncSession,
    user: User,
    dialog: BotDialog,
    sign: str,
    data: dict[str, Any],
) -> list[Reply]:
    # pending (today/month) задачи 5–6 заберут; сейчас только подтверждаем знак.
    await storage.save(session, dialog, state=states.IDLE, data={})
    saved = Reply(text=texts.SIGN_SAVED.format(sign=sign))
    pending = data.get("pending")
    if pending in {"today", "month"}:
        # Знак только что появился — гороскоп ещё заглушка (задачи 5–6).
        return [saved, Reply(text=texts.STUB_HOROSCOPE), menu_reply(user)]
    return [saved, menu_reply(user)]


def _date_in_range(value: date) -> bool:
    if value.year < MIN_BIRTH_YEAR:
        return False
    return value <= datetime.now(UTC).date()
