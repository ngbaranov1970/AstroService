"""Кодовые слова КАРТА / ПАРА в комментариях под постами сообщества.

Под постом бот не отвечает никогда: ключ сообщества не даёт wall.createComment
(ошибка 15, docs/vk-bot-plan.md, задача 10). Писать человеку можно — сценарий
ядра уходит в личку; нельзя — уведомление владельцу со ссылкой на комментарий.
Зовёт vk_callback.py фоновой задачей, ядро получает обычный Incoming.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chatbot import router
from app.chatbot.types import Incoming, Reply
from app.config import get_settings
from app.db import get_sessionmaker
from app.models import Event, Platform, User
from app.services import events, users
from app.vk import api
from app.vk import bot as vk_bot

logger = logging.getLogger(__name__)

# Первое сообщение в личке: без него ответ бота выглядит как спам из ниоткуда.
TO_DM_TEXT = "Увидела твой комментарий под постом — продолжим здесь 👇"

OWNER_TEXT = (
    "💬 {name} написал(а) {code} под постом, а писать в личку нельзя — "
    "разрешения нет.\nОтветить вручную: {link}"
)


def code_word(text: str) -> str | None:
    """«КАРТА» / «ПАРА» из текста комментария, иначе None.

    «КАРТА 9.03.1987 Егорьевск» тоже КАРТА — как в личке.
    """
    normalized = router.normalize(text)
    if normalized == "карта" or normalized.startswith("карта "):
        return "КАРТА"
    if normalized == "пара":
        return "ПАРА"
    return None


def parse_comment(obj: dict[str, Any]) -> dict[str, Any] | None:
    """Поля wall_reply_new, нужные дальше; None — комментарий нас не касается.

    Отбрасывает комментарии самого сообщества (from_id < 0), чужие стены
    и всё, кроме кодовых слов.
    """
    group_id = get_settings().vk_group_id
    from_id = obj.get("from_id")
    post_id = obj.get("post_id")
    comment_id = obj.get("id")
    text = obj.get("text")
    if not (
        isinstance(from_id, int) and from_id > 0
        and isinstance(post_id, int) and isinstance(comment_id, int)
        and isinstance(text, str)
    ):
        return None
    if group_id is not None and obj.get("owner_id") != -group_id:
        return None
    code = code_word(text)
    if code is None:
        return None
    return {
        "from_id": from_id, "post_id": post_id, "comment_id": comment_id,
        "text": text, "code": code,
    }


def comment_link(post_id: int, comment_id: int) -> str:
    """Ссылка, открывающая пост сразу на нужном комментарии."""
    group_id = get_settings().vk_group_id
    return f"https://vk.ru/wall-{group_id}_{post_id}?reply={comment_id}"


def can_message(user: User) -> bool:
    """Писать в личку можно: разрешение поймано и доставка не закрыта (900/902)."""
    return user.vk_messages_allowed_at is not None and user.bot_blocked_at is None


async def _owner_notified(session: AsyncSession, user_id: int, post_id: int) -> bool:
    """Уведомление про этого человека под этим постом уже уходило."""
    stmt = (
        select(Event.id)
        .where(
            Event.name == events.BOT_COMMENT_NO_DM,
            Event.user_id == user_id,
            Event.props["post_id"].astext == str(post_id),
        )
        .limit(1)
    )
    return await session.scalar(stmt) is not None


async def process_comment(obj: dict[str, Any]) -> None:
    """Обработать wall_reply_new после ответа Callback; ошибки логируются без ПД."""
    comment = parse_comment(obj)
    if comment is None:
        return
    from_id, post_id, code = comment["from_id"], comment["post_id"], comment["code"]
    key = f"c{comment['comment_id']}"
    user_id = None
    try:
        async with get_sessionmaker()() as session:
            # Разрешение писать здесь НЕ ставим: комментарий согласием на
            # переписку не является, а рассылка без согласия — бан сообщества.
            user = await users.get_or_create(
                session, platform=Platform.VK, external_id=str(from_id),
                source="vk_group", subscribed=False,
            )
            user_id = user.id

            if can_message(user):
                # ref — откуда ПАРА: по нему воронка отличает пост от слова в личке.
                incoming = Incoming(
                    platform="vk", external_id=str(from_id), text=comment["text"],
                    ref="pair_post" if code == "ПАРА" else None,
                )
                outgoing = await router.handle(
                    session, user, incoming, is_member=vk_bot.member_check(from_id)
                )
                await events.log(
                    session, events.BOT_COMMENT_TO_DM, user_id=user.id, post_id=post_id, code=code
                )
                await session.commit()
                logger.info("VK-комментарий: user_id=%s post_id=%s %s → личка", user_id, post_id, code)
                await vk_bot.deliver(from_id, key, [Reply(text=TO_DM_TEXT), *outgoing], user_id)
                return

            if await _owner_notified(session, user.id, post_id):
                return
            await users.fill_vk_name(user)
            if await _notify_owner(user, code, post_id, comment["comment_id"], key):
                await events.log(
                    session, events.BOT_COMMENT_NO_DM, user_id=user.id, post_id=post_id, code=code
                )
            await session.commit()
            logger.info("VK-комментарий: user_id=%s post_id=%s %s → владельцу", user_id, post_id, code)
    except Exception as error:  # noqa: BLE001 — ошибка фона не должна менять ответ Callback
        detail = str(error)[:120] if isinstance(error, api.VkError) else "-"
        logger.error(
            "VK-комментарий: сбой user_id=%s post_id=%s error_type=%s detail=%s",
            user_id, post_id, type(error).__name__, detail,
        )


async def _notify_owner(user: User, code: str, post_id: int, comment_id: int, key: str) -> bool:
    """Написать владельцу; True — можно засчитать уведомление.

    Без VIP_OWNER_VK_ID тоже True: слать некому, а событие нужно статистике.
    Отказ VK — False: следующий комментарий того же человека попробует снова.
    """
    owner_id = get_settings().vip_owner_vk_id
    if owner_id is None:
        logger.warning("VK-комментарий: VIP_OWNER_VK_ID не задан — владельцу не пишем")
        return True
    text = OWNER_TEXT.format(
        name=user.platform_username or f"vk.ru/id{user.external_id}",
        code=code,
        link=comment_link(post_id, comment_id),
    )
    result = await api.send_message(
        owner_id, text, random_id=vk_bot._random_id(owner_id, key, 0)
    )
    if not result.delivered:
        logger.error("VK-комментарий: владельцу не ушло, VK %s", result.error_code)
        return False
    return True
