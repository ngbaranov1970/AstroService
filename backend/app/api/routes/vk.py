"""JSON-маршруты сообщества VK: разрешение писать и состояние подписки.

Слой тонкий: принять запрос, позвать app/services/users.py и app/vk/api.py,
отдать схему. Те же сервисные функции дёргает обработчик событий Callback
API (app/web/routes/vk_callback.py) — разрешить и запретить сообщения можно
и в диалоге с сообществом, и здесь, поведение обязано совпадать.

Своя запись разрешения обязательна: список разрешивших VK не отдаёт вовсе,
и без неё теряется единственный канал повторного контакта
(docs/astro-context.md, «Контур сообщества VK»).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status

from app.api.deps import CurrentUser, SessionDep
from app.models import Platform, User
from app.schemas.vk import GroupStateOut
from app.services import events
from app.services import users as users_service
from app.vk import api as vk_api

router = APIRouter(prefix="/vk", tags=["vk"])


@router.get("/group-state", response_model=GroupStateOut)
async def group_state(user: CurrentUser) -> GroupStateOut:
    """Состоит ли человек в сообществе и разрешил ли писать.

    Спрашивает VK по сети, поэтому вынесено отдельным запросом, а не полем
    в /auth/vk: вход не должен ждать чужой сервер. Ошибку наружу не
    выпускает — недоступный VK даёт `joined=null`, а не отказ.
    """
    if user.platform is not Platform.VK:
        # Не из VK — блока подписки на экране нет вовсе, и сетевой вызов был
        # бы напрасным.
        return GroupStateOut(joined=None, messages_allowed=False)

    joined = await vk_api.is_group_member(int(user.external_id))
    return GroupStateOut(
        joined=joined,
        messages_allowed=user.vk_messages_allowed_at is not None,
    )


@router.post("/messages-allowed", status_code=status.HTTP_204_NO_CONTENT)
async def messages_allowed(user: CurrentUser, session: SessionDep) -> Response:
    """Человек разрешил сообществу писать — запоминаем это у себя.

    Зовётся после того, как VK Bridge ответил согласием на
    `VKWebAppAllowMessagesFromGroup`. Ответ пустой: фронту от него ничего не
    нужно, состояние он и так знает.
    """
    _require_vk(user)

    if users_service.mark_vk_messages_allowed(user):
        await events.log(session, events.GROUP_MESSAGES_ALLOWED, user_id=user.id)
    # Согласие означает, что чёрного списка и запрета приватности больше нет.
    # Погашенный по коду 900/902 иначе в рассылку не вернётся.
    users_service.clear_delivery_block(user)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/messages-denied", status_code=status.HTTP_204_NO_CONTENT)
async def messages_denied(user: CurrentUser, session: SessionDep) -> Response:
    """Человек отказался от писем — гасим разрешение у себя.

    Обратного метода к `VKWebAppAllowMessagesFromGroup` у VK Bridge нет: на
    стороне VK разрешение останется, но рассылка берёт получателей по нашему
    флагу, и писать мы перестанем сразу.
    """
    _require_vk(user)

    if users_service.mark_vk_messages_denied(user):
        await events.log(session, events.GROUP_MESSAGES_DENIED, user_id=user.id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _require_vk(user: User) -> None:
    """409, если человек вошёл не из VK.

    Разрешение писать — про канал VK. Рассылка отбирает получателей по
    `platform == VK`, так что web-строке этот флаг ничего не даёт, а событие
    воронки от неё добавило бы ложную конверсию.
    """
    if user.platform is not Platform.VK:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Сообщения сообщества доступны только при входе из VK",
        )
