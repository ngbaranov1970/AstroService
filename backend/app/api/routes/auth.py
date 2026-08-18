"""Вход в JSON-API: параметры запуска VK → наш сессионный токен.

Единственная точка входа всего API: остальные маршруты требуют готовый
токен и никого не заводят. Регистрации в сервисе нет — первый заход из VK
и есть регистрация.

Тот же обмен делает и веб-стенд в app/web/deps.py, но там он вплетён в
каждый запрос (личность едет в адресе), а здесь это отдельный шаг: фронт
меняет параметры запуска на токен один раз при старте и дальше носит его
в заголовке.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status

from app.api.deps import SessionDep
from app.config import get_settings
from app.models import Platform
from app.schemas.auth import SessionOut, UserOut, VkLaunchIn
from app.services import auth, events
from app.services import users as users_service

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
