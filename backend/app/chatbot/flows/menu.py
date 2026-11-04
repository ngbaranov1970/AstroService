"""Главное меню, выбор знака, гороскоп на сегодня и на месяц, подписка.

Подписка — явный opt-in: новые пользователи бота создаются с subscribed=False
(app/vk/bot.py), флаг включают только кнопки «Подписаться» / «Гороскоп
каждый день». Выбор знака флаг не меняет.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.chatbot import parse, states, storage, texts
from app.chatbot.types import Button, Incoming, Reply
from app.llm.prompts import monthly_horoscope
from app.llm.prompts.daily_horoscope import SIGN_ORDER
from app.models import BotDialog, User
from app.services import broadcast, content, events
from app.services import charts as charts_service
from app.services import sun_sign as sun_sign_service
from app.services import users as users_service
from astro_core.transits import sun_sign_bounds, sun_sign_on

logger = logging.getLogger(__name__)

_SIGN_BY_NORM = {s.lower().replace("ё", "е"): s for s in SIGN_ORDER}


def knows_sign(user: User) -> bool:
    """Знак известен: поле sun_sign или своя карта (рассылка берёт знак из неё)."""
    return bool(user.sun_sign or user.primary_chart_id)


def menu_reply(user: User) -> Reply:
    """Меню inline-кнопками под сообщением.

    Обычная клавиатура (снизу) на десктопе VK часто не рисуется или схлопывается
    после ответа без keyboard — кнопки пропадают, текст меню остаётся.
    Inline виден на вебе и в приложении (проверили 02.11.2026).
    """
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
        inline=True,
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
    if cmd == "today":
        if not knows_sign(user):
            return await start_ask_sign(
                session, user, dialog, pending="today"
            )
        return await reply_today(session, user)
    if cmd == "month":
        if not knows_sign(user):
            return await start_ask_sign(
                session, user, dialog, pending="month"
            )
        return await reply_month(session, user)
    if cmd == "subscribe":
        # Рассылка без знака человеку ничего не пришлёт (broadcast.recipients
        # требует знак) — сначала спрашиваем знак, подписку доделает
        # _after_sign_saved.
        if not knows_sign(user):
            return await start_ask_sign(
                session, user, dialog, pending="subscribe"
            )
        return reply_subscribe(user)
    if cmd == "unsubscribe":
        return reply_unsubscribe(user)
    return None


def reply_subscribe(user: User) -> list[Reply]:
    """Явный opt-in на утреннюю рассылку; повторное нажатие безвредно."""
    users_service.set_subscribed(user, True)
    return [Reply(text=texts.SUBSCRIBED), menu_reply(user)]


def reply_unsubscribe(user: User) -> list[Reply]:
    """Выключить рассылку; разрешение VK на сообщения не трогаем."""
    users_service.set_subscribed(user, False)
    return [Reply(text=texts.UNSUBSCRIBED), menu_reply(user)]


async def start_ask_sign(
    session: AsyncSession,
    user: User,
    dialog: BotDialog,
    *,
    pending: str | None = None,
) -> list[Reply]:
    """Открыть выбор знака двумя сообщениями (лимит VK — 10 inline-кнопок)."""
    data: dict[str, Any] = {}
    if pending:
        data["pending"] = pending
    await storage.save(session, dialog, state=states.ASK_SIGN, data=data)
    if user.sun_sign:
        text = texts.ASK_SIGN_AGAIN.format(sign=user.sun_sign)
    else:
        text = texts.ASK_SIGN
    return _sign_replies(text)


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

    guess = parse.parse_birth(normalized)
    if guess.bad_date:
        return [Reply(text=texts.SIGN_DATE_RANGE)]
    if guess.birth_date is None:
        return [Reply(text=texts.SIGN_BAD_DATE)]
    return await _apply_birth_date(session, user, dialog, guess.birth_date, data)


def parse_birth_date(text: str) -> date | None:
    """Дата в любой форме, что понимает parse.parse_birth; None — не дата."""
    return parse.parse_birth(text).birth_date


def _sign_replies(text: str) -> list[Reply]:
    """Две inline-клавиатуры: 9 знаков, затем 3 + «Ввести дату».

    У messages.send для inline максимум 10 кнопок (ошибка 911) — 12 знаков
    и дата в одном сообщении VK отвергает.
    """
    sign_buttons = [
        Button(sign, {"cmd": "set_sign", "sign": sign}) for sign in SIGN_ORDER
    ]
    first = _button_rows(sign_buttons[:9], width=3)
    second = _button_rows(sign_buttons[9:], width=3)
    second.append([Button(texts.BTN_ENTER_DATE, {"cmd": "sign_date"})])
    return [
        Reply(text=text, buttons=first, inline=True),
        Reply(text=texts.ASK_SIGN_MORE, buttons=second, inline=True),
    ]


def _button_rows(buttons: list[Button], *, width: int) -> list[list[Button]]:
    return [buttons[i : i + width] for i in range(0, len(buttons), width)]


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
    await storage.save(session, dialog, state=states.IDLE, data={})
    saved = Reply(text=texts.SIGN_SAVED.format(sign=sign))
    pending = data.get("pending")
    if pending == "today":
        return [saved, *(await reply_today(session, user))]
    if pending == "month":
        return [saved, *(await reply_month(session, user))]
    if pending == "subscribe":
        return [saved, *reply_subscribe(user)]
    return [saved, menu_reply(user)]


async def reply_today(session: AsyncSession, user: User) -> list[Reply]:
    """Гороскоп на сегодня из поста рассылки; нет поста — «готовится» + меню."""
    sign = await _resolve_sign(session, user)
    if sign is None:
        # knows_sign мог сработать по битой карте без Солнца и без sun_sign.
        return [Reply(text=texts.TODAY_NOT_READY), menu_reply(user)]

    try:
        letters = await broadcast.letters_for(session, content.today_msk())
        text = letters[sign]
    except KeyError:
        # Пост есть, но знака в нём нет — тот же ответ, что «ещё готовится».
        logger.warning(
            "chatbot.today missing sign user_id=%s sign=%s", user.id, sign
        )
        return [Reply(text=texts.TODAY_NOT_READY), menu_reply(user)]
    except LookupError:
        logger.info(
            "chatbot.today not ready user_id=%s sign=%s", user.id, sign
        )
        return [Reply(text=texts.TODAY_NOT_READY), menu_reply(user)]

    await events.log(session, events.BOT_TODAY_SHOWN, user_id=user.id, sign=sign)
    return [_horoscope_reply(user, text)]


async def reply_month(session: AsyncSession, user: User) -> list[Reply]:
    """Гороскоп на текущий месяц; нет поста — «готовится» + меню."""
    sign = await _resolve_sign(session, user)
    if sign is None:
        return [Reply(text=texts.MONTH_NOT_READY), menu_reply(user)]

    month_start = monthly_horoscope.month_start_of(content.today_msk())
    try:
        letters = await broadcast.month_letters_for(session, month_start)
        text = letters[sign]
    except KeyError:
        logger.warning(
            "chatbot.month missing sign user_id=%s sign=%s month=%s",
            user.id,
            sign,
            month_start,
        )
        return [Reply(text=texts.MONTH_NOT_READY), menu_reply(user)]
    except LookupError:
        # Нет поста — сигнал владельцу в лог: cron/CLI ещё не прогнали месяц.
        logger.info(
            "chatbot.month not ready user_id=%s sign=%s month=%s",
            user.id,
            sign,
            month_start,
        )
        return [Reply(text=texts.MONTH_NOT_READY), menu_reply(user)]

    await events.log(session, events.BOT_MONTH_SHOWN, user_id=user.id, sign=sign)
    return [_horoscope_reply(user, text)]


def _horoscope_reply(user: User, text: str) -> Reply:
    """Текст знака; неподписанному — inline «Гороскоп каждый день»."""
    buttons: list[list[Button]] = []
    if not user.subscribed:
        buttons = [[Button(texts.BTN_DAILY, {"cmd": "subscribe"})]]
    return Reply(text=text, buttons=buttons, inline=bool(buttons))


async def _resolve_sign(session: AsyncSession, user: User) -> str | None:
    """Знак как в рассылке: из своей карты, иначе users.sun_sign."""
    if user.primary_chart_id is not None:
        chart = await charts_service.get_chart(
            session, user.primary_chart_id, user_id=user.id
        )
        if chart is not None and chart.calc:
            from_chart = broadcast.sun_sign(chart.calc)
            if from_chart:
                return from_chart
    return user.sun_sign

