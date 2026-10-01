"""Вход в приложение из обычного браузера: VK ID → наш сессионный токен.

Место в слоях: браузер → ЭТОТ ФАЙЛ → vk/id.py (обмен кода) и services/
(строка пользователя, событие, JWT). Своей логики личности здесь нет.

ПОЧЕМУ ЭТО В app/web/, А НЕ В JSON-API

Сюда браузер приходит переходом по ссылке и уходит редиректом, а не за
JSON: обмен кода требует, чтобы человек СХОДИЛ на id.vk.ru и вернулся.
Ручка `/api/v1/auth/vk` устроена иначе — она принимает уже готовые
параметры запуска от фронта.

ВХОД ВЕДЁТ В ТУ ЖЕ СТРОКУ, ЧТО И MINI APP

VK ID отдаёт тот же `vk_user_id`, поэтому пользователь заводится с
`platform=VK`, а не отдельной веб-личностью. Сверено живым обменом
02.09.2026 (docs/astro-context.md, «Веб-вход через VK ID»).
"""

from __future__ import annotations

import hmac
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models import Platform
from app.services import auth, events
from app.services import users as users_service
from app.vk import id as vk_id
from app.web.routes.redirects import APP_PATH

logger = logging.getLogger(__name__)

router = APIRouter(include_in_schema=False)

SessionDep = Annotated[AsyncSession, Depends(get_session)]

# Cookie с секретами одной попытки входа: code_verifier и state через точку.
# Отдельная от astro_uid намеренно — та живёт год и опознаёт браузер, эта
# нужна ровно между уходом на id.vk.ru и возвратом.
STATE_COOKIE = "astro_vkid"

# Столько же, сколько VK отводит коду возврата (expires_in=600).
STATE_MAX_AGE = 600

# Точка не входит в алфавит base64url и token_urlsafe, поэтому годится как
# разделитель: разъехаться со значениями она не может.
_COOKIE_SEP = "."


def _fail_url() -> str:
    """Куда вернуть человека, если войти не удалось.

    Признак читает гейт фронта и предлагает войти заново. Отдельной страницы
    с ошибкой нет намеренно: экран входа уже нарисован во фронте.
    """
    return f"{APP_PATH}?auth_error=1"


@router.get("/auth/vkid/start")
async def start() -> RedirectResponse:
    """Отправить человека в окно входа VK ID.

    VK ID не настроен в окружении — возвращаем на экран входа с признаком
    ошибки, а не 503: человек нажал кнопку и должен увидеть внятный экран,
    а не страницу сервера.
    """
    settings = get_settings()
    if not settings.vk_id_enabled:
        logger.error("VK ID не настроен — вход из браузера невозможен")
        return RedirectResponse(_fail_url(), status.HTTP_303_SEE_OTHER)

    started = vk_id.build_authorize_url()

    response = RedirectResponse(started.url, status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        STATE_COOKIE,
        f"{started.verifier}{_COOKIE_SEP}{started.state}",
        max_age=STATE_MAX_AGE,
        # Путь покрывает и /auth/vkid/start, и возврат на /auth/vkid: на
        # остальные страницы cookie не уходит вовсе.
        path="/auth/vkid",
        httponly=True,
        # lax: возврат от id.vk.ru — переход по ссылке, cookie дойдёт.
        samesite="lax",
        secure=settings.is_prod,
    )
    return response


@router.get("/auth/vkid")
async def callback(
    request: Request,
    session: SessionDep,
    code: Annotated[str | None, Query()] = None,
    device_id: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    """Принять возврат от VK ID и впустить в приложение.

    При успехе редирект на `/app/?s=<JWT>`, при любой неудаче — на тот же
    адрес с `auth_error=1`. Коммитит сам: нового пользователя и событие
    открытия иначе откатит закрытие сессии.
    """
    saved = request.cookies.get(STATE_COOKIE)
    if saved is None or _COOKIE_SEP not in saved:
        # Человек шёл дольше десяти минут, чистил cookie или пришёл по
        # чужой ссылке возврата.
        logger.info("Возврат VK ID без cookie попытки")
        return _forget_state(RedirectResponse(_fail_url(), status.HTTP_303_SEE_OTHER))

    verifier, _, expected_state = saved.partition(_COOKIE_SEP)

    # Отказ в окне VK приходит без кода: человек нажал «Отмена».
    if not code or not device_id or not state:
        logger.info("Возврат VK ID без кода — вход отклонён")
        return _forget_state(RedirectResponse(_fail_url(), status.HTTP_303_SEE_OTHER))

    # compare_digest, а не `==`: то же правило, что при сверке подписи VK.
    if not hmac.compare_digest(state, expected_state):
        logger.warning("state в возврате VK ID не совпал с выданным")
        return _forget_state(RedirectResponse(_fail_url(), status.HTTP_303_SEE_OTHER))

    try:
        vk_user_id = await vk_id.exchange_code(code, device_id, verifier)
    except vk_id.VkIdError as err:
        logger.warning("Обмен кода VK ID не удался: %s", err)
        return _forget_state(RedirectResponse(_fail_url(), status.HTTP_303_SEE_OTHER))

    user = await users_service.get_or_create(
        session,
        platform=Platform.VK,
        external_id=vk_user_id,
        source=vk_id.SOURCE_VK_ID,
    )
    await users_service.fill_vk_name(user)
    # platform="web" отличает браузерный заход от запуска Mini App: в users
    # они одна строка, и различить их можно только здесь.
    await events.log(
        session,
        events.APP_OPENED,
        user_id=user.id,
        platform="web",
    )
    await session.commit()

    # Площадка web, хотя строка пользователя VK-шная: человек сидит в браузере,
    # и запрет продавать на него не распространяется.
    token = auth.issue_session(user.id, auth.SURFACE_WEB)
    # Токен в адресе, как и в VK: cookie фронт не читает, а другого способа
    # передать сессию собранному SPA при первой загрузке нет. Фронт стирает
    # параметр из адресной строки сразу после чтения.
    target = f"{APP_PATH}?s={token}"
    return _forget_state(RedirectResponse(target, status.HTTP_303_SEE_OTHER))


def _forget_state(response: RedirectResponse) -> RedirectResponse:
    """Стереть cookie попытки: она одноразовая, второй раз код не обменять."""
    response.delete_cookie(STATE_COOKIE, path="/auth/vkid")
    return response
