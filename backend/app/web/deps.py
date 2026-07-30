"""Кто пришёл на страницу стенда: cookie → пользователь, и охрана админки.

Это единственное место во всём веб-слое, где выясняется, чей запрос мы
обрабатываем. Хендлеры в router.py уже получают готового `User` и про
cookie не знают ничего.

Почему так, а не «прочитать cookie там, где понадобилось»: платформ будет
три. Сейчас это веб-стенд (случайный UUID в cookie), дальше добавятся
Telegram Web App (подпись `initData`) и VK Mini App (параметр `sign`).
Каждая — это своя проверка подлинности, но одинаковый результат: строка в
`users`. Если разбор cookie расползётся по хендлерам, добавление второй
платформы придётся делать в каждом из них.

Общий слой авторизации здесь НЕ проектируется намеренно (решение
2026-07-29): двух других платформ ещё нет, и что у них окажется общего,
станет видно, когда обе будут написаны. Сейчас — простейший честный
вариант на ~100 строк комментариев и 30 строк кода.

Три вещи, которые файл делает:

1. `current_user` — зависимость FastAPI: достаёт из cookie UUID (или
   выдаёт новый) и превращает его в строку `users` через сервис.
2. `install_session_cookie` — middleware, который проставляет cookie в
   ответ. Почему не прямо в зависимости — расписано у самой функции.
3. `require_admin` — HTTP Basic на `/admin`: там даты и места рождения
   живых людей, то есть персональные данные.
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

# Имя cookie. Префикса `__Host-` (который браузер отдаёт только по HTTPS и
# только на тот же домен) намеренно нет: стенд первые дни живёт по голому
# IP, и такая cookie там просто не сохранится.
COOKIE_NAME = "astro_uid"

# Год. Тестер заходит раз в несколько дней, и терять его карты между
# визитами нельзя — иначе он каждый раз начинает с пустого списка.
COOKIE_MAX_AGE = 365 * 24 * 60 * 60

# Имя параметра из секретной ссылки для тестеров: /?t=<секрет из .env>.
TESTER_QUERY_PARAM = "t"

# Ключ в request.state, через который зависимость передаёт middleware
# идентификатор для cookie. Строка вынесена в константу, чтобы опечатка в
# одном из двух мест не превращалась в молча не работающую cookie.
_STATE_UID = "web_uid"

# Помечает пользователей, пришедших на стенд, — на будущее, когда рядом
# появятся реальные каналы привлечения.
SOURCE_WEB_TESTER = "web_tester"


def _read_uid(request: Request) -> str | None:
    """Достать идентификатор из cookie, если он там есть и выглядит как UUID.

    Проверка формата обязательна. Cookie приходит от клиента, то есть это
    любые байты, какие он захочет прислать: в `users.external_id` колонка
    `varchar(64)`, и строка длиннее просто уронит вставку ошибкой базы.
    Мусор молча игнорируем и выдаём новый идентификатор — для человека это
    выглядит как «зашёл впервые», и это честное поведение: восстановить его
    карты мы всё равно не можем.

    От подделки это не защищает (валидный UUID может сочинить кто угодно),
    и не должно: cookie здесь — не пароль, а «какая корзина твоя».
    Единственное, что можно украсть, зная чужой UUID, — чужие тестовые
    карты; для стенда риск приемлемый, в продукте вход будет через VK.
    """
    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        return None
    try:
        # str(UUID(...)) заодно приводит запись к каноничной: без этого
        # один и тот же идентификатор в верхнем и нижнем регистре дал бы
        # двух разных пользователей.
        return str(uuid.UUID(raw))
    except ValueError:
        logger.debug("Cookie %s не похожа на UUID, выдаю новый", COOKIE_NAME)
        return None


async def current_user(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """Зависимость: `user: Annotated[User, Depends(current_user)]` в хендлере.

    Возвращает всегда — незнакомого человека мы не отвергаем, а заводим.
    Регистрации в сервисе нет по замыслу.

    Коммит здесь есть, хотя общее правило (см. app/db.py) — «транзакцией
    управляет вызывающий». Причина: пользователь создаётся и на обычном
    `GET /`, где хендлеру коммитить нечего, и без коммита новая строка
    откатилась бы вместе с сессией. Коммит ровно один и самодостаточный:
    появление пользователя не зависит от того, чем кончится остальная
    работа запроса.
    """
    uid = _read_uid(request)
    if uid is None:
        # uuid4 — случайные 122 бита. Угадать чужой перебором нельзя,
        # а координации между процессами (в отличие от счётчика) не нужно.
        uid = str(uuid.uuid4())

    # Middleware ниже положит это значение в cookie ответа. Делаем это
    # ВСЕГДА, а не только для новых: так срок жизни cookie продлевается на
    # год при каждом визите, и активный тестер не теряет карты внезапно.
    setattr(request.state, _STATE_UID, uid)

    user = await users_service.get_or_create(
        session,
        platform=Platform.WEB,
        external_id=uid,
        source=SOURCE_WEB_TESTER,
    )

    # Секретная ссылка /?t=<секрет> делает тестером того, кто по ней пришёл.
    # Проверяется на каждом запросе, а не только на «/», потому что стоит
    # это один разбор query string, а забыть подключить проверку к нужному
    # маршруту — легко.
    if not user.is_tester and _tester_token_matches(request):
        user.is_tester = True
        # user_id — не персональные данные, его можно писать в лог
        # (правило CLAUDE.md: ни даты рождения, ни места, ни имени).
        logger.info("Пользователь user_id=%s стал тестером по ссылке", user.id)
        # Момент раздачи ссылок видно по этому событию: сколько человек ею
        # реально воспользовались и когда. Пишется в той же транзакции, что
        # и сам флаг, — коммит ниже один на всё.
        await events.log(session, events.TESTER_ACTIVATED, user_id=user.id)

    await session.commit()
    return user


def _tester_token_matches(request: Request) -> bool:
    """Совпадает ли `?t=...` с секретом из .env.

    Если `WEB_TESTER_TOKEN` не задан — механизм выключен, тестером не
    станет никто. Дефолт именно такой (а не «не задан — пускаем всех»)
    потому, что забытая на сервере переменная должна закрывать дверь:
    тестеру открыт полный разбор без оплаты, то есть за наши деньги.
    """
    expected = get_settings().web_tester_token
    if expected is None:
        return False
    given = request.query_params.get(TESTER_QUERY_PARAM)
    if not given:
        return False
    # compare_digest вместо `==`: обычное сравнение строк выходит на первом
    # различающемся символе, и по времени ответа секрет подбирается посимвольно.
    return secrets.compare_digest(given, expected.get_secret_value())


class SessionCookieMiddleware(BaseHTTPMiddleware):
    """Проставляет cookie с идентификатором в ответ.

    Почему это middleware, а не `response.set_cookie(...)` внутри
    зависимости — тонкость FastAPI, на которой легко потерять полдня.
    Зависимость может получить объект `Response` и что-то в него записать,
    НО эти заголовки попадают в ответ только если хендлер вернул данные
    (dict, модель, шаблон). Если хендлер возвращает готовый `Response`
    своими руками — а у нас это все `POST`, которые отвечают редиректом на
    `/r/<token>`, — FastAPI отдаёт его как есть, и cookie бесследно
    пропадает. Ровно на этих запросах пользователь и создаётся впервые.

    Middleware же видит финальный ответ, каким бы способом тот ни был
    сделан. Работает он через `request.state`, который живёт в scope
    запроса и поэтому один и тот же у middleware и у зависимости.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        uid = getattr(request.state, _STATE_UID, None)
        if uid is None:
            # Запрос не трогал current_user: /health, /static, 404.
            # Заводить cookie незачем.
            return response

        settings = get_settings()
        response.set_cookie(
            COOKIE_NAME,
            uid,
            max_age=COOKIE_MAX_AGE,
            path="/",
            # JavaScript до cookie не дотягивается: на стенде скриптов нет
            # вовсе, а запрет снимает целый класс проблем с XSS.
            httponly=True,
            # lax — cookie уходит при обычном переходе по ссылке (тестер
            # приходит по ссылке из мессенджера, и там она нужна), но не
            # при фоновых запросах с чужих сайтов.
            samesite="lax",
            # Только на проде: на dev-стенде по http cookie с secure=True
            # браузер просто выбросит, и вход перестанет работать.
            secure=settings.is_prod,
        )
        return response


def install_session_cookie(app) -> None:
    """Подключить middleware к приложению. Зовётся из app/main.py."""
    app.add_middleware(SessionCookieMiddleware)


# --- Админка ---------------------------------------------------------------

# auto_error=True: при отсутствии заголовка авторизации FastAPI сам ответит
# 401 с WWW-Authenticate, то есть браузер покажет окно логина и пароля.
_basic = HTTPBasic(auto_error=True)


def require_admin(
    credentials: Annotated[HTTPBasicCredentials, Depends(_basic)],
) -> str:
    """HTTP Basic на /admin. Возвращает имя вошедшего (для логов).

    Админка показывает список карт: имена, даты и места рождения живых
    людей. Это чужие персональные данные, и в открытом интернете страница
    висеть не может — а стенд по определению в открытом интернете, иначе
    тестеры до него не доберутся.

    Basic Auth достаточен ровно потому, что за ним не продукт, а инструмент
    для двух человек. Пароль ходит в заголовке почти открытым текстом, так
    что обязательное условие — HTTPS (его даёт Caddy на шаге выкатки).

    Пароль не задан → 503, а не «пускаем всех»: та же логика, что у секрета
    тестеров. Забытая переменная окружения должна закрывать дверь.
    """
    settings = get_settings()
    if settings.admin_password is None:
        logger.error("ADMIN_PASSWORD не задан — админка закрыта (503)")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Админка не настроена",
        )

    # Оба сравнения — compare_digest и оба выполняются всегда (не через
    # `and`, который выйдет раньше): иначе по времени ответа видно, угадано
    # ли имя пользователя. Сравниваем байты, потому что compare_digest не
    # принимает строки с не-ASCII символами.
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
