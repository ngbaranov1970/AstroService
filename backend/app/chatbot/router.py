"""Маршрутизация сообщения: сброс → payload → кодовое слово → FSM → меню.

Flows: menu (меню, знак, гороскопы, подписка), natal (КАРТА), vip (платный
разбор). Долгую работу flows возвращают как Job — её выполняет run_job.
Про VK не знает — только Incoming/Reply.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.chatbot import states, storage, texts
from app.chatbot.flows import menu, natal, vip
from app.chatbot.types import AppButton, Incoming, Job, Outgoing, Reply
from app.models import BotDialog, User
from app.services import events

logger = logging.getLogger(__name__)

_CODE_KARTA = "карта"
_CODE_PARA = "пара"
_RESET_TEXTS = frozenset({"меню", "начать", "старт", "start"})
_WS = re.compile(r"\s+")
_VIP_STATES = frozenset({states.VIP_NAME, states.VIP_GENDER, states.VIP_CONFIRM})


def normalize(text: str) -> str:
    """Нижний регистр, ё→е, края и повторные пробелы убрать."""
    return _WS.sub(" ", text.strip().lower().replace("ё", "е"))


async def handle(
    session: AsyncSession,
    user: User,
    incoming: Incoming,
) -> Sequence[Outgoing]:
    """Разобрать входящее и вернуть ответы (и, возможно, Job последним).

    Сессию не коммитит. Не пишет в лог текст сообщения — только user_id и state.
    """
    dialog = await storage.load(session, user.id)
    payload = incoming.payload or {}
    normalized = normalize(incoming.text)

    logger.info(
        "chatbot.handle user_id=%s state=%s has_payload=%s",
        user.id,
        dialog.state,
        bool(incoming.payload),
    )

    if _is_reset(normalized, payload):
        await storage.reset(session, dialog)
        return [menu.menu_reply(user)]

    if "cmd" in payload:
        return await _dispatch_cmd(session, user, dialog, payload)

    if normalized == _CODE_PARA:
        return await _start_pair(session, user, dialog)
    if normalized == _CODE_KARTA or normalized.startswith(_CODE_KARTA + " "):
        # «КАРТА 9.03.1987 Егорьевск» — данные сразу за кодовым словом.
        rest = incoming.text.strip()[len(_CODE_KARTA) :].strip()
        return await natal.start(session, user, dialog, first_text=rest or None)

    if dialog.state == states.ASK_SIGN:
        return await menu.handle_ask_sign(session, user, dialog, incoming)
    if natal.is_collecting(dialog.state) or dialog.state == states.NATAL_WAIT:
        return await natal.handle_text(session, user, dialog, incoming)
    if dialog.state in _VIP_STATES:
        return await vip.handle_text(session, user, dialog, incoming)

    # Человек сразу прислал «9,03,1987, время не помню, Егорьевск» без КАРТА.
    if natal.looks_like_birth_data(incoming.text):
        return await natal.start(session, user, dialog, first_text=incoming.text)
    return [menu.menu_reply(user)]


async def run_job(session: AsyncSession, user: User, job: Job) -> Sequence[Outgoing]:
    """Выполнить отложенную работу из handle. Коммитит сама работа."""
    dialog = await storage.load(session, user.id)
    if job.name == natal.JOB_REPORT:
        return await natal.run_report(session, user, dialog)
    logger.error("chatbot.run_job unknown job=%s user_id=%s", job.name, user.id)
    return []


def _is_reset(normalized: str, payload: dict[str, Any]) -> bool:
    if normalized in _RESET_TEXTS:
        return True
    if payload.get("command") == "start":
        return True
    return payload.get("cmd") == "menu"


async def _dispatch_cmd(
    session: AsyncSession,
    user: User,
    dialog: BotDialog,
    payload: dict[str, Any],
) -> Sequence[Outgoing]:
    cmd = str(payload.get("cmd", ""))
    if cmd in {"natal", "karta"}:
        return await natal.start(session, user, dialog)
    if cmd in {"pair", "para"}:
        return await _start_pair(session, user, dialog)

    for flow in (natal, vip, menu):
        handled = await flow.dispatch(session, user, dialog, payload)
        if handled is not None:
            return handled
    return [menu.menu_reply(user)]


async def _start_pair(
    session: AsyncSession, user: User, dialog: BotDialog
) -> Sequence[Outgoing]:
    # Кодовое слово перебивает текущий сценарий — обратно в idle.
    await storage.save(session, dialog, state=states.IDLE)
    await events.log(session, events.BOT_PAIR_LINK, user_id=user.id)
    return [
        Reply(
            text=texts.PAIR_TEXT,
            buttons=[[AppButton(label=texts.BTN_OPEN_PAIR, hash="pair")]],
            inline=True,
        )
    ]
