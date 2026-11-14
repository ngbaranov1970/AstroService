"""Маршрутизация сообщения: сброс → payload → кодовое слово → FSM → меню.

Flows: menu (меню, знак, гороскопы, подписка), natal (КАРТА), vip (платный
разбор), pair (ПАРА: тизер и заказ разбора пары), gate (КАРТА и ПАРА только
подписчикам сообщества). Долгую работу flows возвращают как Job — её выполняет run_job.
Про VK не знает — только Incoming/Reply.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.chatbot import states, storage
from app.chatbot.flows import gate, menu, natal, pair, vip
from app.chatbot.types import Incoming, Job, MemberCheck, Outgoing
from app.models import BotDialog, User

logger = logging.getLogger(__name__)

_CODE_KARTA = "карта"
_CODE_PARA = "пара"
_RESET_TEXTS = frozenset({"меню", "начать", "старт", "start"})
_WS = re.compile(r"\s+")
_VIP_STATES = frozenset({states.VIP_NAME, states.VIP_GENDER, states.VIP_CONFIRM})
_PAIR_STATES = frozenset(
    {states.PAIR_SELF, states.PAIR_PARTNER, states.PAIR_TEASER, states.PAIR_ORDER}
)


def normalize(text: str) -> str:
    """Нижний регистр, ё→е, края и повторные пробелы убрать."""
    return _WS.sub(" ", text.strip().lower().replace("ё", "е"))


async def handle(
    session: AsyncSession,
    user: User,
    incoming: Incoming,
    *,
    is_member: MemberCheck | None = None,
) -> Sequence[Outgoing]:
    """Разобрать входящее и вернуть ответы (и, возможно, Job последним).

    is_member — проверка подписки на сообщество от адаптера; без неё КАРТА
    и ПАРА открыты всем. Сессию не коммитит. Не пишет в лог текст сообщения — только user_id и state.
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
        return await _dispatch_cmd(session, user, dialog, payload, is_member)

    if normalized == _CODE_PARA:
        # ref из vk.me/…?ref= или из комментария (pair_post); иначе слово в личке.
        return await _start_pair(
            session, user, dialog, is_member, ref=incoming.ref or pair.REF_DM
        )
    if normalized == _CODE_KARTA or normalized.startswith(_CODE_KARTA + " "):
        # «КАРТА 9.03.1987 Егорьевск» — данные сразу за кодовым словом.
        rest = incoming.text.strip()[len(_CODE_KARTA) :].strip()
        return await _start_natal(
            session, user, dialog, is_member, first_text=rest or None
        )

    if dialog.state == states.ASK_SIGN:
        return await menu.handle_ask_sign(session, user, dialog, incoming)
    if natal.is_collecting(dialog.state) or dialog.state == states.NATAL_WAIT:
        return await natal.handle_text(session, user, dialog, incoming)
    if dialog.state in _VIP_STATES:
        return await vip.handle_text(session, user, dialog, incoming)
    if dialog.state in _PAIR_STATES:
        return await pair.handle_text(session, user, dialog, incoming)

    # Человек сразу прислал «9,03,1987, время не помню, Егорьевск» без КАРТА.
    if natal.looks_like_birth_data(incoming.text):
        return await _start_natal(
            session, user, dialog, is_member, first_text=incoming.text
        )
    return [menu.menu_reply(user)]


async def run_job(session: AsyncSession, user: User, job: Job) -> Sequence[Outgoing]:
    """Выполнить отложенную работу из handle. Коммитит сама работа."""
    dialog = await storage.load(session, user.id)
    if job.name == natal.JOB_REPORT:
        return await natal.run_report(session, user, dialog)
    if job.name == pair.JOB_TEASER:
        return await pair.run_teaser(session, user, dialog)
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
    is_member: MemberCheck | None,
) -> Sequence[Outgoing]:
    cmd = str(payload.get("cmd", ""))
    if cmd in {"natal", "karta"}:
        return await _start_natal(session, user, dialog, is_member)
    if cmd in {"pair", "para"}:
        return await _start_pair(session, user, dialog, is_member, ref=pair.REF_MENU)
    if cmd == gate.CMD_CHECK:
        # «Я подписался»: проверяем заново и продолжаем с места остановки.
        if payload.get("target") == gate.TARGET_PAIR:
            # ref входа гейт сохранил в своей метке — берём до того, как он её снимет.
            return await _start_pair(
                session, user, dialog, is_member,
                ref=gate.saved_text(dialog) or pair.REF_DM, retry=True,
            )
        return await _start_natal(
            session, user, dialog, is_member,
            first_text=gate.saved_text(dialog), retry=True,
        )

    for flow in (natal, vip, pair, menu):
        handled = await flow.dispatch(session, user, dialog, payload)
        if handled is not None:
            return handled
    return [menu.menu_reply(user)]


async def _start_natal(
    session: AsyncSession,
    user: User,
    dialog: BotDialog,
    is_member: MemberCheck | None,
    *,
    first_text: str | None = None,
    retry: bool = False,
) -> Sequence[Outgoing]:
    """Бесплатная КАРТА: сначала подписка, потом сбор данных."""
    blocked = await gate.closed(
        session, user, dialog, is_member, gate.TARGET_NATAL,
        first_text=first_text, retry=retry,
    )
    if blocked is not None:
        return blocked
    return await natal.start(session, user, dialog, first_text=first_text)


async def _start_pair(
    session: AsyncSession,
    user: User,
    dialog: BotDialog,
    is_member: MemberCheck | None,
    *,
    ref: str,
    retry: bool = False,
) -> Sequence[Outgoing]:
    """Бесплатный тизер ПАРЫ: сначала подписка, потом сбор данных.

    ref едет через гейт в его метке (first_text): после «Я подписался»
    вход остаётся тем же, каким был до просьбы подписаться.
    """
    blocked = await gate.closed(
        session, user, dialog, is_member, gate.TARGET_PAIR, first_text=ref, retry=retry
    )
    if blocked is not None:
        return blocked
    return await pair.start(session, user, dialog, ref=ref)
