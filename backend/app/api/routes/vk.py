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
from app.config import get_settings
from app.models import Platform, User
from app.schemas.vk import GroupStateOut, SunSignIn, SunSignOut, VipCtaIn
from app.services import events
from app.services import users as users_service
from app.vk import api as vk_api
from astro_core.transits import sun_sign_on

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
        return GroupStateOut(
            joined=None, messages_allowed=False, subscribed=False, group_id=None
        )

    joined = await vk_api.is_group_member(int(user.external_id))
    return GroupStateOut(
        joined=joined,
        messages_allowed=user.vk_messages_allowed_at is not None,
        subscribed=user.subscribed,
        group_id=get_settings().vk_group_id,
        sun_sign=user.sun_sign,
    )


@router.post("/messages-allowed", status_code=status.HTTP_204_NO_CONTENT)
async def messages_allowed(user: CurrentUser, session: SessionDep) -> Response:
    """Человек включил рассылку — поднимаем и разрешение, и подписку.

    Зовётся после согласия в окне `VKWebAppAllowMessagesFromGroup`, а вне
    Mini App — сам по себе: разрешение VK там уже записано, включить письма
    обратно можно без окна. Ответ пустой: фронту от него ничего не нужно,
    состояние он и так знает.
    """
    _require_vk(user)

    # Два флага разной природы: разрешение канала VK и согласие на письма.
    # Событие пишем, если изменился любой, — оба означают «рассылка включена»,
    # а повторное нажатие не должно добавлять в воронку лишнюю конверсию.
    granted = users_service.mark_vk_messages_allowed(user)
    resubscribed = users_service.set_subscribed(user, True)
    if granted or resubscribed:
        await events.log(session, events.GROUP_MESSAGES_ALLOWED, user_id=user.id)
    # Согласие означает, что чёрного списка и запрета приватности больше нет.
    # Погашенный по коду 900/902 иначе в рассылку не вернётся.
    users_service.clear_delivery_block(user)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/messages-denied", status_code=status.HTTP_204_NO_CONTENT)
async def messages_denied(user: CurrentUser, session: SessionDep) -> Response:
    """Человек отказался от писем — гасим подписку, разрешение оставляем.

    Обратного метода к `VKWebAppAllowMessagesFromGroup` у VK Bridge нет, и
    стирать `vk_messages_allowed_at` тут нельзя: в самом VK разрешение
    остаётся, а вернуть нашу запись о нём можно только новым окном Bridge —
    вне Mini App человек оказался бы отписан навсегда. Рассылка проверяет оба
    флага, поэтому писать мы перестаём сразу.
    """
    _require_vk(user)

    if users_service.set_subscribed(user, False):
        await events.log(session, events.GROUP_MESSAGES_DENIED, user_id=user.id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/sun-sign", response_model=SunSignOut)
async def sun_sign(body: SunSignIn, user: CurrentUser, session: SessionDep) -> SunSignOut:
    """Знак Солнца по одной дате — короткий путь к рассылке без карты.

    День смены знака — не ошибка, а `sign=null`: фронт ведёт человека в
    онбординг. Записанный раньше знак при этом не стирается
    (docs/astro-context.md, «Знак для рассылки — без карты»).
    """
    _require_vk(user)

    sign = sun_sign_on(body.birth_date)
    if sign is None:
        await events.log(session, events.SUN_SIGN_ENTERED, user_id=user.id, cusp=True)
    else:
        users_service.set_sun_sign(user, sign)
        await events.log(session, events.SUN_SIGN_ENTERED, user_id=user.id, sign=sign)
    await session.commit()
    return SunSignOut(sign=sign)


@router.post("/vip-cta", status_code=status.HTTP_204_NO_CONTENT)
async def vip_cta(body: VipCtaIn, user: CurrentUser, session: SessionDep) -> Response:
    """Человек нажал «Напиши нам» — пишем событие воронки VIP.

    Без _require_vk: ссылка на личку сообщества — не оплата, блок стоит и в
    браузере. Каждое нажатие — событие: уникальных считает выборка по user_id.
    """
    await events.log(session, events.VIP_CTA_CLICKED, user_id=user.id, place=body.place)
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
