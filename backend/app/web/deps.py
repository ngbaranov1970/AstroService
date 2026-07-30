"""Кто пришёл на страницу стенда: cookie → `User`, плюс охрана админки.

Единственное место веб-слоя, где выясняется, чей это запрос: хендлеры
router.py получают готового пользователя и про cookie не знают. Рядом
встанут Telegram Web App (подпись `initData`) и VK Mini App (`sign`) —
проверка подлинности у каждой своя, результат один и тот же, строка в
`users`. Общий слой авторизации намеренно не проектируется, пока платформа
одна (решение 2026-07-29, docs/astro-context.md).
"""

from __future__ import annotations

import logging
import secrets
import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings
from app.db import get_session
from app.models import Platform, User
from app.services import events
from app.services import users as users_service

logger = logging.getLogger(__name__)

# Без префикса `__Host-`: он требует HTTPS, а стенд первые дни живёт по
# голому IP — такая cookie там просто не сохранится.
COOKIE_NAME = "astro_uid"

# Год: тестер заходит раз в несколько дней и не должен терять свои карты.
COOKIE_MAX_AGE = 365 * 24 * 60 * 60

# Имя параметра из секретной ссылки для тестеров: /?t=<секрет из .env>.
TESTER_QUERY_PARAM = "t"

# Ключ в request.state: зависимость кладёт uid, middleware забирает. В
# константе — чтобы опечатка в одном из двух мест не ломала cookie молча.
_STATE_UID = "web_uid"

# Значение users.source для пришедших на стенд.
SOURCE_WEB_TESTER = "web_tester"


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


async def current_user(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """Зависимость FastAPI: пользователь стенда по cookie, незнакомого — заводит.

    Возвращает всегда, отказа нет: регистрации в сервисе нет по замыслу.
    Коммитит сама, в обход общего правила «транзакцией управляет вызывающий»
    (app/db.py): на обычном `GET /` хендлеру коммитить нечего, и новая
    строка откатилась бы вместе с сессией.
    """
    uid = _read_uid(request)
    if uid is None:
        # uuid4 — случайные 122 бита: чужой не угадать перебором, а
        # координация между процессами (в отличие от счётчика) не нужна.
        uid = str(uuid.uuid4())

    # Кладём ВСЕГДА, не только для новых: middleware перевыставит cookie, и
    # срок её жизни продлевается на год при каждом визите.
    setattr(request.state, _STATE_UID, uid)

    user = await users_service.get_or_create(
        session,
        platform=Platform.WEB,
        external_id=uid,
        source=SOURCE_WEB_TESTER,
    )

    # Проверяем на каждом запросе, а не только на «/»: стоит это один разбор
    # query string, а забыть подключить проверку к маршруту — легко.
    if not user.is_tester and _tester_token_matches(request):
        user.is_tester = True
        logger.info("Пользователь user_id=%s стал тестером по ссылке", user.id)
        # По событию видно, сколько розданных ссылок сработало и когда.
        await events.log(session, events.TESTER_ACTIVATED, user_id=user.id)

    await session.commit()
    return user


def _tester_token_matches(request: Request) -> bool:
    """Совпадает ли `?t=...` с секретом из .env; токен не задан → False.

    Дефолт именно такой: забытая на сервере переменная должна закрывать
    дверь, за ней тестеру открыт полный разбор без оплаты, за наши деньги.
    """
    expected = get_settings().web_tester_token
    if expected is None:
        return False
    given = request.query_params.get(TESTER_QUERY_PARAM)
    if not given:
        return False
    # compare_digest, а не `==`: обычное сравнение выходит на первом
    # различающемся символе, и секрет подбирается по времени ответа.
    return secrets.compare_digest(given, expected.get_secret_value())


class SessionCookieMiddleware(BaseHTTPMiddleware):
    """Проставляет cookie с идентификатором в ответ.

    Не `response.set_cookie` внутри зависимости: записанные там заголовки
    теряются, если хендлер вернул готовый `Response` — а это все наши POST
    с редиректом на `/r/<token>`, ровно те запросы, где пользователь и
    создаётся впервые. Middleware видит финальный ответ в любом случае.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        uid = getattr(request.state, _STATE_UID, None)
        if uid is None:
            # Запрос не трогал current_user: /health, /static, 404.
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


# --- Админка ---------------------------------------------------------------

# auto_error=True: без заголовка авторизации FastAPI сам ответит 401 с
# WWW-Authenticate, и браузер покажет окно логина и пароля.
_basic = HTTPBasic(auto_error=True)


def require_admin(
    credentials: Annotated[HTTPBasicCredentials, Depends(_basic)],
) -> str:
    """HTTP Basic на /admin. Возвращает имя вошедшего, иначе бросает 401 или 503.

    За страницей — имена, даты и места рождения живых людей, а стенд по
    определению в открытом интернете. Basic хватает на инструмент для двух
    человек, но пароль идёт почти открытым текстом: обязателен HTTPS (Caddy).
    Пароль не задан → 503, а не «пускаем всех», как и с секретом тестеров.
    """
    settings = get_settings()
    if settings.admin_password is None:
        logger.error("ADMIN_PASSWORD не задан — админка закрыта (503)")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Админка не настроена",
        )

    # Оба сравнения выполняются всегда (не через `and`, который выйдет
    # раньше): иначе по времени ответа видно, угадано ли имя. Байты, а не
    # строки, — compare_digest не принимает не-ASCII.
    user_ok = secrets.compare_digest(
        credentials.username.encode("utf-8"), settings.admin_user.encode("utf-8")
    )
    password_ok = secrets.compare_digest(
        credentials.password.encode("utf-8"),
        settings.admin_password.get_secret_value().encode("utf-8"),
    )
    if not (user_ok and password_ok):
        logger.warning("Неудачный вход в админку")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль",
            # Без этого заголовка браузер не покажет окно повторного ввода.
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username
