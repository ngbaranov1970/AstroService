"""Адаптер VK: входящее сообщение → ядро диалога → ответы через VK API.

Фоновая задача владеет сессией БД. Клавиатуры и формат VK остаются здесь,
app.chatbot получает только платформенно-независимые Incoming и Reply.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Sequence
from typing import Any

from app.chatbot import router
from app.chatbot.types import AppButton, Button, Incoming, Job, LinkButton, Outgoing, Reply
from app.config import get_settings
from app.db import get_sessionmaker
from app.models import Platform
from app.services import events, users
from app.vk import api

logger = logging.getLogger(__name__)

_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)
_BOLD = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)


def strip_markdown(text: str) -> str:
    """Убрать заголовки и жирное начертание: VK не отображает Markdown."""
    text = _HEADING.sub(lambda match: f"\n\n{match.group(1)}\n\n", text)
    text = _BOLD.sub(r"\1", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def split_text(text: str, limit: int = 4000) -> list[str]:
    """Разделить текст по абзацам, строкам или словам, не превышая limit."""
    if limit < 1:
        raise ValueError("limit должен быть положительным")
    parts: list[str] = []
    text = text.strip()
    while len(text) > limit:
        cut = 0
        for separator in ("\n\n", "\n", " "):
            cut = text.rfind(separator, 0, limit + 1)
            if cut > 0:
                break
        if cut <= 0:
            cut = limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        parts.append(text)
    return parts


def keyboard_json(reply: Reply) -> str | None:
    """Собрать клавиатуру VK; неверные размеры или настройки дают ValueError."""
    if not reply.buttons:
        return None
    if len(reply.buttons) > (6 if reply.inline else 10):
        raise ValueError("Слишком много рядов клавиатуры")
    # Inline: VK режет не только ряды, но и сумму кнопок (ошибка 911 при >10).
    total = sum(len(row) for row in reply.buttons)
    if reply.inline and total > 10:
        raise ValueError("Inline-клавиатура VK: не больше 10 кнопок")
    rows = []
    for row in reply.buttons:
        if not 1 <= len(row) <= 5:
            raise ValueError("В ряду клавиатуры должно быть от 1 до 5 кнопок")
        buttons = []
        for button in row:
            item: dict[str, Any]
            if isinstance(button, Button):
                item = {
                    "action": {
                        "type": "text",
                        "label": button.label,
                        "payload": json.dumps(button.payload, ensure_ascii=False),
                    },
                    "color": button.color,
                }
            elif isinstance(button, LinkButton):
                item = {
                    "action": {
                        "type": "open_link",
                        "label": button.label,
                        "link": button.url,
                    }
                }
            elif isinstance(button, AppButton):
                settings = get_settings()
                if settings.vk_app_id is None or settings.vk_group_id is None:
                    raise ValueError("Для open_app нужны VK_APP_ID и VK_GROUP_ID")
                item = {
                    "action": {
                        "type": "open_app",
                        "label": button.label,
                        "app_id": settings.vk_app_id,
                        "owner_id": -settings.vk_group_id,
                        "hash": button.hash,
                    }
                }
            else:
                raise TypeError("Неизвестный тип кнопки")
            buttons.append(item)
        rows.append(buttons)
    keyboard: dict[str, Any] = {"inline": reply.inline, "buttons": rows}
    if not reply.inline:
        keyboard["one_time"] = False
    return json.dumps(keyboard, ensure_ascii=False)


def _payload(value: Any) -> dict | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


def _random_id(peer_id: int, key: str, part: int) -> int:
    raw = f"vk_bot:{peer_id}:{key}:{part}".encode()
    return int.from_bytes(hashlib.sha256(raw).digest()[:4], "big") & 0x7FFFFFFF or 1


def _split_job(outgoing: Sequence[Outgoing]) -> tuple[list[Reply], Job | None]:
    """Ответы до первой Job и сама Job; всё после Job ядро не возвращает."""
    replies: list[Reply] = []
    for item in outgoing:
        if isinstance(item, Job):
            return replies, item
        replies.append(item)
    return replies, None


async def _send_replies(
    peer_id: int,
    key: str,
    replies: list[Reply],
    part_index: int,
    user_id: int | None,
) -> int:
    """Отправить ответы частями; вернуть следующий номер части или -1 при отказе VK."""
    for reply in replies:
        keyboard = keyboard_json(reply)
        parts = split_text(strip_markdown(reply.text))
        for index, part in enumerate(parts):
            result = await api.send_message(
                peer_id,
                part,
                random_id=_random_id(peer_id, key, part_index),
                keyboard=keyboard if index == len(parts) - 1 else None,
            )
            if result.error_code is not None:
                logger.warning(
                    "VK-бот: отказ отправки user_id=%s code=%s", user_id, result.error_code
                )
                return -1
            part_index += 1
    return part_index


async def deliver(
    peer_id: int, key: str, outgoing: Sequence[Outgoing], user_id: int
) -> None:
    """Отправить ответы ядра, выполняя Job по очереди; исключения не глотает.

    key — основа random_id: повтор с тем же key VK не задвоит.
    """
    part_index = 0
    while True:
        replies, job = _split_job(outgoing)
        part_index = await _send_replies(peer_id, key, replies, part_index, user_id)
        if part_index < 0 or job is None:
            return
        # Долгая работа (разбор 30–60 с) — в своей сессии, уже после «считаю».
        async with get_sessionmaker()() as session:
            job_user = await users.get_by_id(session, user_id)
            if job_user is None:
                return
            outgoing = await router.run_job(session, job_user, job)
            await session.commit()


async def process_message(obj: dict[str, Any]) -> None:
    """Обработать личное message_new после ответа Callback; ошибки логируются без ПД."""
    message = obj.get("message") or {}
    sender = message.get("from_id")
    peer_id = message.get("peer_id")
    message_id = message.get("conversation_message_id")
    if (
        message.get("out")
        or not isinstance(sender, int)
        or sender <= 0
        or not isinstance(peer_id, int)
        or peer_id != sender
        or not isinstance(message_id, int)
    ):
        return
    user_id = None
    try:
        async with get_sessionmaker()() as session:
            user = await users.get_or_create(
                session,
                platform=Platform.VK,
                external_id=str(sender),
                source="vk_group",
                subscribed=False,
            )
            user_id = user.id
            # ref приходит только с первым сообщением после перехода по vk.me/…?ref=.
            ref = message.get("ref")
            if isinstance(ref, str) and ref:
                ref_source = message.get("ref_source")
                await events.log(
                    session,
                    events.BOT_REF,
                    user_id=user.id,
                    ref=ref[:64],
                    ref_source=ref_source[:64] if isinstance(ref_source, str) else None,
                )
            incoming = Incoming(
                platform="vk",
                external_id=str(sender),
                text=message.get("text") or "",
                payload=_payload(message.get("payload")),
                ref=message.get("ref"),
            )
            outgoing = await router.handle(session, user, incoming)
            # Сохраняем состояние до отправки: следующий ответ уже относится к нему.
            await session.commit()

        await deliver(peer_id, str(message_id), outgoing, user_id)
    except Exception as error:  # noqa: BLE001 — ошибка фона не должна менять ответ Callback
        # VkError — ответ API (код/описание), без текста человека. Остальное —
        # только тип: str(error) может содержать дату рождения из исключения.
        detail = str(error)[:120] if isinstance(error, api.VkError) else "-"
        logger.error(
            "VK-бот: сбой user_id=%s error_type=%s detail=%s",
            user_id,
            type(error).__name__,
            detail,
        )
