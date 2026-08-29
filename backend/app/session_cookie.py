"""Личность браузера в cookie: чтение идентификатора и его выдача в ответ.

Общий низкий слой, а не часть веб-стенда: ту же cookie читают и страницы на
Jinja (app/web/deps.py), и вход JSON-API (app/api/routes/auth.py). Лежит в
app/ именно поэтому — зависимость api → web была бы направлена не в ту
сторону, а дубликат имени cookie в двух местах однажды разъедется.

Про базу и про VK файл не знает: на входе Request, на выходе строка-UUID.
Кто такой владелец этого идентификатора, решает вызывающий.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings

logger = logging.getLogger(__name__)

# Без префикса `__Host-`: он требует HTTPS, а стенд первые дни жил по голому
# IP — такая cookie там просто не сохранится.
COOKIE_NAME = "astro_uid"

# Год: тестер заходит раз в несколько дней и не должен терять свои карты.
COOKIE_MAX_AGE = 365 * 24 * 60 * 60

# Значение users.source для пришедших через браузер.
SOURCE_WEB_TESTER = "web_tester"

# Ключ в request.state: сюда кладут uid, middleware забирает. В константе —
# чтобы опечатка в одном из двух мест не ломала cookie молча.
_STATE_UID = "web_uid"


def take_uid(request: Request) -> tuple[str, bool]:
    """Идентификатор браузера и признак «cookie была предъявлена».

    Мусор в cookie и её отсутствие обрабатываются одинаково: выдаётся новый
    UUID. Значение кладётся в request.state всегда, в том числе для уже
    известного, — тогда middleware перевыставит cookie и продлит её на год.
    """
    uid = _read_uid(request)
    known = uid is not None
    if uid is None:
        # uuid4 — случайные 122 бита: чужой не угадать перебором, а
        # координация между процессами (в отличие от счётчика) не нужна.
        uid = str(uuid.uuid4())

    setattr(request.state, _STATE_UID, uid)
    return uid, known


def _read_uid(request: Request) -> str | None:
    """Вернуть UUID из cookie или None, если её нет или в ней мусор.

    Формат проверяем потому, что cookie — данные клиента: строка длиннее
    `varchar(64)` уронит вставку в `users.external_id`. От подделки это не
    защищает и не должно — cookie здесь не пароль, а «какая корзина твоя».
    """
    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        return None
    try:
        # str(UUID(...)) заодно приводит запись к каноничной: иначе один
        # и тот же идентификатор в разном регистре даст двух пользователей.
        return str(uuid.UUID(raw))
    except ValueError:
        logger.debug("Cookie %s не похожа на UUID, выдаю новый", COOKIE_NAME)
        return None


class SessionCookieMiddleware(BaseHTTPMiddleware):
    """Проставляет cookie с идентификатором в ответ.

    Не `response.set_cookie` внутри зависимости: записанные там заголовки
    теряются, если хендлер вернул готовый `Response` — FastAPI отдаёт его как
    есть (`routing.py`, `raw_response` возвращается напрямую). А это все наши
    POST с редиректом на `/r/<token>`, ровно те запросы, где пользователь и
    создаётся впервые. Middleware видит финальный ответ в любом случае.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        uid = getattr(request.state, _STATE_UID, None)
        if uid is None:
            # Запрос не трогал опознание: /health, /static, 404.
            return response

        settings = get_settings()
        response.set_cookie(
            COOKIE_NAME,
            uid,
            max_age=COOKIE_MAX_AGE,
            path="/",
            # Скриптов на стенде нет вовсе, а запрет снимает класс XSS-проблем.
            httponly=True,
            # lax: cookie уходит при переходе по ссылке из мессенджера
            # (так тестер и приходит), но не при запросах с чужих сайтов.
            samesite="lax",
            # На dev-стенде по http браузер выбросит cookie с secure=True.
            secure=settings.is_prod,
        )
        return response


def install_session_cookie(app) -> None:
    """Подключить middleware к приложению. Зовётся из app/main.py."""
    app.add_middleware(SessionCookieMiddleware)
