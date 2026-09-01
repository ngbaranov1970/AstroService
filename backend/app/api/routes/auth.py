"""Вход в JSON-API: параметры запуска VK → наш сессионный токен.

Единственная точка входа всего API: остальные маршруты требуют готовый
токен и никого не заводят. Регистрации в сервисе нет — первый заход из VK
и есть регистрация. Второй маршрут, `/auth/web`, опознаёт по cookie и
работает только вне прода: он нужен, чтобы отлаживать фронт в обычном
браузере, боевого веб-входа у нас пока нет.

Обмен идёт отдельным шагом: фронт меняет параметры запуска на токен один
раз при старте и дальше носит его в заголовке. В снятом стенде было иначе —
личность ехала в адресе и проверялась на каждом запросе.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, status

from app.api.deps import SessionDep
from app.config import get_settings
from app.models import Platform
from app.schemas.auth import SessionOut, UserOut, VkLaunchIn
from app.services import auth, events
from app.services import users as users_service
from app.session_cookie import SOURCE_WEB_TESTER, take_uid

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/vk", response_model=SessionOut)
async def login_vk(payload: VkLaunchIn, session: SessionDep) -> SessionOut:
    """Проверить подпись запуска VK и выдать токен сессии на сутки.

    401 — подпись не сошлась, протухла или выдана другому приложению;
    503 — VK-ветка не настроена в этом окружении. Коммитит сам: нового
    пользователя и событие открытия иначе откатит закрытие сессии.
    """
    if not get_settings().vk_enabled:
        # 503, а не 500: код исправен, не хватает настроек окружения. На
        # проде это означает пустой VK_SECURE_KEY или SESSION_SECRET.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Вход через VK не настроен",
        )

    try:
        vk_id = auth.verify_vk_launch(payload.params)
    except auth.LaunchError as err:
        # Причина уходит в лог, но не клиенту: подсказка «подпись неверна»
        # против «параметры протухли» помогает только тому, кто подбирает.
        logger.info("Параметры запуска VK отклонены: %s", err)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Параметры запуска не приняты — откройте приложение заново",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None

    user = await users_service.get_or_create(
        session,
        platform=Platform.VK,
        external_id=vk_id,
        source=auth.SOURCE_VK,
    )
    # Метки запуска есть только здесь: внутри приложения фронт ходит уже с
    # токеном, и откуда человек пришёл, дальше взять негде.
    await events.log(
        session,
        events.APP_OPENED,
        user_id=user.id,
        ref=payload.params.get(auth.VK_REF_PARAM),
        platform=payload.params.get(auth.VK_PLATFORM_PARAM),
    )
    await session.commit()

    return SessionOut(
        token=auth.issue_session(user.id),
        expires_in=auth.SESSION_TTL,
        user=UserOut.model_validate(user),
    )


@router.post("/web", response_model=SessionOut)
async def login_web(request: Request, session: SessionDep) -> SessionOut:
    """Вход по cookie браузера — для разработки фронта. В проде 404.

    503 — не задан SESSION_SECRET. Cookie в ответ ставит middleware
    (app/session_cookie.py), хендлеру достаточно дёрнуть take_uid.
    """
    settings = get_settings()
    if settings.is_prod:
        # В проде маршрута нет, и 404 говорит ровно это: отдельная веб-личность
        # (cookie на браузер) разъезжается с VK-строкой того же человека, и
        # боевой веб-вход будет через VK ID — docs/astro-context.md, «Идентичность»
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")

    if settings.session_secret is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Вход не настроен: нет SESSION_SECRET",
        )

    uid, _ = take_uid(request)
    user = await users_service.get_or_create(
        session,
        platform=Platform.WEB,
        external_id=uid,
        source=SOURCE_WEB_TESTER,
    )
    # События открытия здесь нет, в отличие от VK-ветки: маршрут живёт только
    # в разработке, и его заходы исказили бы воронку
    await session.commit()

    return SessionOut(
        token=auth.issue_session(user.id),
        expires_in=auth.SESSION_TTL,
        user=UserOut.model_validate(user),
    )
