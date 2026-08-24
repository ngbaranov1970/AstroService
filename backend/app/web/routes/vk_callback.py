"""Callback API сообщества VK: события «разрешил / запретил / написал».

Место в слоях: сервер VK → ЭТОТ ФАЙЛ → services/users.py. Своей логики
нет — только разбор события, проверка подлинности и коммит.

ЗАЧЕМ ЭТО ВООБЩЕ НУЖНО

Писать человеку первым ВКонтакте разрешает только тому сообществу, которому
он это позволил, а СПИСОК ПОЗВОЛИВШИХ ЧЕРЕЗ API НЕ ОТДАЁТСЯ НИ ОДНИМ
МЕТОДОМ. Значит, список ведём мы сами, и единственный источник записей —
события отсюда: не поймали разрешение в момент нажатия, потеряли навсегда.
Обратное так же важно: пропущенный message_deny означает рассылку в
закрытую дверь, а это прямая дорога к бану сообщества за спам.

ПОЧЕМУ ОТВЕТ — ПРОСТОЙ ТЕКСТ

VK ждёт от сервера ровно "ok" (а на первое событие — строку подтверждения)
и сверяет ответ побайтово. Обычный для FastAPI возврат словаря или строки
даёт JSON, то есть "ok" в кавычках, — этого достаточно, чтобы кабинет
показал «Сервер вернул неправильный ответ». Отсюда PlainTextResponse во
всех ветках.
"""

from __future__ import annotations

import hmac
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models import Platform
from app.services import events, users

logger = logging.getLogger(__name__)

router = APIRouter(include_in_schema=False)

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _ok() -> PlainTextResponse:
    """Единственный ответ, который VK считает успехом для обычного события.

    Функция, а не константа: объект ответа Starlette отдаётся в конкретное
    соединение, и общий на все запросы однажды аукнется гонкой заголовков.
    """
    return PlainTextResponse("ok")


# Метка канала для тех, кто разрешил писать, ни разу не открыв приложение:
# пришёл из сообщества, а не из каталога VK.
_SOURCE = "vk_group"


@router.post("/vk/callback")
async def vk_callback(request: Request, session: SessionDep) -> PlainTextResponse:
    """Событие сообщества VK. Всегда отвечает 200, кроме чужих запросов.

    Возвращает строку подтверждения на type=confirmation и "ok" на всё
    остальное, включая события, которые нам неинтересны: молчание или код
    ошибки VK читает как сбой и шлёт событие снова.
    """
    settings = get_settings()
    if not settings.vk_callback_enabled:
        # Ключей в .env нет — проверить подлинность события нечем. Отвечаем
        # 503, а не "ok": так несделанная настройка видна в кабинете сразу,
        # а не через неделю по пустому списку подписчиков.
        logger.error("Callback VK: VK_CALLBACK_SECRET или VK_CALLBACK_CONFIRMATION не заданы")
        return PlainTextResponse("not configured", status_code=status.HTTP_503_SERVICE_UNAVAILABLE)

    try:
        body: dict[str, Any] = await request.json()
    except ValueError:
        logger.warning("Callback VK: тело не разбирается как JSON")
        return _ok()

    event_type = body.get("type")

    # Подтверждение адреса идёт ДО сверки секрета намеренно: кабинет
    # присылает confirmation и в том случае, когда секрет там ещё не
    # сохранён, — и тогда сверка не дала бы подтвердить сервер вовсе.
    # Утечка не страшна: строка подтверждения привязана к паре «наше
    # сообщество + наш сервер» и постороннему бесполезна.
    if event_type == "confirmation":
        return PlainTextResponse(settings.vk_callback_confirmation or "")

    # Подписи у события нет, адрес открыт всему интернету — общий секрет
    # единственное, что отличает VK от постороннего. compare_digest вместо
    # `==`: обычное сравнение строк выходит из цикла на первом различии и
    # временем ответа выдаёт длину совпавшего префикса.
    expected = settings.vk_callback_secret
    if expected is None or not hmac.compare_digest(
        str(body.get("secret") or ""), expected.get_secret_value()
    ):
        logger.warning("Callback VK: секрет не сошёлся, событие %s отброшено", event_type)
        return PlainTextResponse("forbidden", status_code=status.HTTP_403_FORBIDDEN)

    # Сообщество тоже сверяем: один секрет можно вписать в настройки любого
    # сообщества, а рассылку мы шлём от имени ровно одного.
    group_id = body.get("group_id")
    if settings.vk_group_id is not None and group_id != settings.vk_group_id:
        logger.warning("Callback VK: событие чужого сообщества %s отброшено", group_id)
        return PlainTextResponse("forbidden", status_code=status.HTTP_403_FORBIDDEN)

    obj = body.get("object") or {}
    vk_user_id = _extract_user_id(event_type, obj)
    if vk_user_id is None:
        # Событие не про человека (лайк, запись на стене) или это наше же
        # исходящее сообщение. Подтвердить получение всё равно надо.
        logger.debug("Callback VK: событие %s пропущено", event_type)
        return _ok()

    user = await users.get_or_create(
        session, platform=Platform.VK, external_id=str(vk_user_id), source=_SOURCE
    )

    if event_type == "message_deny":
        if users.mark_vk_messages_denied(user):
            await events.log(session, events.GROUP_MESSAGES_DENIED, user_id=user.id)
        logger.info("Callback VK: пользователь %s запретил сообщения", user.id)
    else:
        # message_allow и message_new: и то и другое даёт право писать.
        # Человек, написавший сам, разрешения через окно не давал, но диалог
        # им и открывается — VK такие сообщения принимает.
        if users.mark_vk_messages_allowed(user):
            await events.log(session, events.GROUP_MESSAGES_ALLOWED, user_id=user.id)
        if event_type == "message_new":
            await events.log(session, events.GROUP_MESSAGE_RECEIVED, user_id=user.id)
        logger.info("Callback VK: пользователь %s разрешил сообщения (%s)", user.id, event_type)

    # Текста сообщения в логе нет и не будет: люди пишут туда дату рождения
    # и вопросы про здоровье (CLAUDE.md, «Персональных данных в логах нет»).
    await session.commit()
    return _ok()


def _extract_user_id(event_type: str | None, obj: dict[str, Any]) -> int | None:
    """Достать id автора события. None — событие нас не касается.

    Форма object у каждого типа своя, общего поля нет: у message_allow и
    message_deny это user_id, у message_new — from_id внутри вложенного
    message (так с версии API 5.103, раньше поля лежали в object напрямую).
    """
    if event_type in ("message_allow", "message_deny"):
        user_id = obj.get("user_id")
        return int(user_id) if isinstance(user_id, int) else None

    if event_type == "message_new":
        message = obj.get("message") or {}
        # out=1 — сообщение написали МЫ. Наша же рассылка возвращается сюда
        # событием, и без этой проверки каждая отправка засчитывалась бы как
        # входящий вопрос человека.
        if message.get("out"):
            return None
        from_id = message.get("from_id")
        # Отрицательный id — сообщество, а не человек: писать ему нельзя,
        # и в users такой строке делать нечего.
        return from_id if isinstance(from_id, int) and from_id > 0 else None

    return None
