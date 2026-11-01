"""Маршрутизация сообщения: payload → кодовое слово → FSM → меню.

Flows (menu/natal/vip/pair) подключим в задачах 4–9; здесь заглушки веток
КАРТА/ПАРА и общее меню. Про VK не знает — только Incoming/Reply.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.chatbot import states, storage, texts
from app.chatbot.types import AppButton, Button, Incoming, Reply
from app.models import BotDialog, User

logger = logging.getLogger(__name__)

_CODE_KARTA = "карта"
_CODE_PARA = "пара"
_RESET_TEXTS = frozenset({"меню", "начать"})
_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Нижний регистр, ё→е, края и повторные пробелы убрать."""
    return _WS.sub(" ", text.strip().lower().replace("ё", "е"))


async def handle(
    session: AsyncSession,
    user: User,
    incoming: Incoming,
) -> list[Reply]:
    """Разобрать входящее и вернуть один или несколько ответов.

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
        return [_menu_reply(user)]

    if "cmd" in payload:
        return await _dispatch_cmd(session, user, dialog, str(payload["cmd"]))

    if normalized == _CODE_KARTA:
        return await _start_natal(session, dialog)
    if normalized == _CODE_PARA:
        return await _start_pair(session, dialog)

    if dialog.state != states.IDLE:
        return [_stub_in_progress()]

    return [_menu_reply(user)]


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
    cmd: str,
) -> list[Reply]:
    """Заглушки кнопок меню: реальные handlers появятся в flows."""
    if cmd in {"natal", "karta"}:
        return await _start_natal(session, dialog)
    if cmd in {"pair", "para"}:
        return await _start_pair(session, dialog)
    # today/month/sign/subscribe — задача 4–7; пока меню.
    return [_menu_reply(user)]


async def _start_natal(session: AsyncSession, dialog: BotDialog) -> list[Reply]:
    await storage.save(session, dialog, state=states.NATAL_DATE, data={})
    return [Reply(text=texts.NATAL_ASK_DATE)]


async def _start_pair(session: AsyncSession, dialog: BotDialog) -> list[Reply]:
    # Кодовое слово перебивает текущий сценарий — обратно в idle.
    await storage.save(session, dialog, state=states.IDLE)
    return [
        Reply(
            text=texts.PAIR_TEXT,
            buttons=[[AppButton(label=texts.BTN_OPEN_PAIR, hash="pair")]],
            inline=True,
        )
    ]


def _menu_reply(user: User) -> Reply:
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


def _stub_in_progress() -> Reply:
    return Reply(
        text=texts.STUB_IN_PROGRESS,
        buttons=[[Button(texts.BTN_MENU, {"cmd": "menu"})]],
        inline=True,
    )
