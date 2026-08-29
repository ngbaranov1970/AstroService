"""Кто пришёл на страницу стенда: VK или cookie → `User`, плюс охрана админки.

Единственное место веб-слоя, где выясняется, чей это запрос: хендлеры
router.py получают готового пользователя и не знают ни про cookie, ни про
подписи. Две ветки опознания, независимые намеренно: у VK носитель личности
— подписанный токен в адресе (`?s=`), потому что в iframe cookie не доходит
(docs/astro-context.md, «В VK личность едет в URL»); у веба — cookie.
Третьей встанет Telegram Web App с подписью `initData`.
"""

from __future__ import annotations

import logging
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models import Platform, User
from app.services import auth, events
from app.services import users as users_service
from app.session_cookie import SOURCE_WEB_TESTER, take_uid

logger = logging.getLogger(__name__)

# Имя параметра из секретной ссылки для тестеров: /?t=<секрет из .env>.
TESTER_QUERY_PARAM = "t"

# Cookie-личность (имя cookie, чтение UUID, middleware) переехала в
# app/session_cookie.py: ту же cookie читает вход JSON-API.

# --- VK --------------------------------------------------------------------

# Имя параметра с нашим сессионным токеном: /r/abc?s=<JWT>. Короткое
# намеренно — оно поедет в каждой ссылке и каждой форме приложения.
SESSION_QUERY_PARAM = "s"

# Имена параметров запуска VK и значение users.source для них живут в
# app/services/auth.py: их делит с этим файлом JSON-API.

# Ключ в request.state: зависимость кладёт свежий токен, шаблоны забирают
# его через session_token() и подставляют в ссылки.
_STATE_TOKEN = "session_token"


async def current_user(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """Зависимость FastAPI: пользователь VK или веба, незнакомого — заводит.

    Для маршрутов, которые ПИШУТ: у карты, отчёта и отзыва должен быть
    владелец. Читающим страницам нужна `optional_user` — она не создаёт.

    Отказ ровно один — 403, когда запрос пришёл из VK, но опознать его не
    удалось (подпись не сошлась, токен протух). Во всех остальных случаях
    возвращает пользователя: регистрации в сервисе нет по замыслу.
    Коммитит сама, в обход общего правила «транзакцией управляет вызывающий»
    (app/db.py): хендлеру POST /places коммитить нечего, и новая строка
    откатилась бы вместе с сессией.
    """
    user = await _resolve(request, session, create=True)
    if user is None:  # pragma: no cover
        raise RuntimeError(
            "current_user не получил пользователя при create=True — веб-ветка "
            "обязана его завести"
        )
    return user


async def optional_user(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User | None:
    """То же самое, но незнакомого НЕ заводит: None вместо новой строки.

    Для читающих страниц (`GET /`, `GET /r/{token}`). Смысл — краулеры: в
    проде 375 строк `users` из 388 завёл обход стартовой страницы. Cookie
    при этом выдаётся всё равно, поэтому человек, который что-нибудь
    нажмёт, принесёт её обратно и получит строку — с тем же идентификатором,
    без потери карт и лимитов.
    """
    return await _resolve(request, session, create=False)


async def _resolve(
    request: Request, session: AsyncSession, *, create: bool
) -> User | None:
    """Общее тело обеих зависимостей; `create` решает судьбу незнакомца."""
    # Секретная ссылка обязана сработать с первого захода, в том числе без
    # cookie: ставить is_tester иначе некому.
    wants_tester = _tester_token_matches(request)

    user = await _vk_user(request, session)
    if user is None:
        user = await _web_user(request, session, create=create or wants_tester)
    if user is None:
        return None

    # Проверяем на каждом запросе, а не только на «/»: стоит это один разбор
    # query string, а забыть подключить проверку к маршруту — легко.
    if wants_tester and not user.is_tester:
        user.is_tester = True
        logger.info("Пользователь user_id=%s стал тестером по ссылке", user.id)
        # По событию видно, сколько розданных ссылок сработало и когда.
        await events.log(session, events.TESTER_ACTIVATED, user_id=user.id)

    await session.commit()
    return user


async def _vk_user(request: Request, session: AsyncSession) -> User | None:
    """Пользователь VK по подписи запуска или по нашему токену из адреса.

    None означает «запрос не из VK» — дальше работает cookie-ветка. Если из
    VK пришли, но опознать не удалось, бросает 403: молча заводить веб-строку
    нельзя, внутри VK cookie не доходит и каждый запрос плодил бы нового
    пользователя.
    """
    if not get_settings().vk_enabled:
        return None

    if request.query_params.get(auth.VK_SIGN_PARAM):
        params = dict(request.query_params)
        try:
            vk_id = auth.verify_vk_launch(params)
        except auth.LaunchError as err:
            # Не отказ сразу: ниже может лежать живой `?s=`. Параметры
            # запуска протухают за час, а страница «готовим разбор»
            # перезагружает себя сама — иначе она однажды отдала бы 403.
            logger.info("Параметры запуска VK отклонены: %s", err)
        else:
            user = await users_service.get_or_create(
                session,
                platform=Platform.VK,
                external_id=vk_id,
                source=auth.SOURCE_VK,
            )
            # `sign` приходит только при запуске приложения — внутри человек
            # ходит уже с `?s=`. Поэтому здесь и только здесь известно, откуда
            # он пришёл и с какого клиента; дальше эти метки взять негде.
            await events.log(
                session,
                events.APP_OPENED,
                user_id=user.id,
                ref=params.get(auth.VK_REF_PARAM),
                platform=params.get(auth.VK_PLATFORM_PARAM),
            )
            _remember_session(request, user.id)
            return user

    raw = request.query_params.get(SESSION_QUERY_PARAM)
    if raw:
        user_id = auth.read_session(raw)
        # Пользователя могли удалить между выпуском токена и запросом —
        # подпись при этом остаётся верной, поэтому проверяем и строку.
        user = await users_service.get_by_id(session, user_id) if user_id else None
        if user is not None:
            _remember_session(request, user.id)
            return user

    if request.query_params.get(auth.VK_SIGN_PARAM) or raw:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Сессия недействительна — откройте приложение заново",
        )
    return None


async def _web_user(
    request: Request, session: AsyncSession, *, create: bool
) -> User | None:
    """Пользователь стенда по cookie; незнакомому выдаётся новый UUID.

    None означает «cookie не предъявлена, а заводить строку не просили» —
    так читающие страницы не плодят пользователей.
    """
    # take_uid заодно кладёт идентификатор в request.state — middleware
    # перевыставит cookie, и её срок жизни продлевается при каждом визите.
    uid, known = take_uid(request)

    # Cookie нет и запрос ничего не пишет — в базу не идём. Cookie при этом
    # выдана: краулер её не вернёт и следа не оставит, а человек вернёт
    # следующим же запросом и получит строку с тем же идентификатором.
    if not known and not create:
        return None

    return await users_service.get_or_create(
        session,
        platform=Platform.WEB,
        external_id=uid,
        source=SOURCE_WEB_TESTER,
    )


def _remember_session(request: Request, user_id: int) -> None:
    """Выпустить свежий токен и положить его в request.state для шаблонов.

    Выпускается на КАЖДОМ запросе, а не только при входе: так суточный срок
    жизни отсчитывается от последнего действия, и активный человек сессию не
    теряет. HMAC на это дешевле, чем поход в базу.
    """
    setattr(request.state, _STATE_TOKEN, auth.issue_session(user_id))


def session_token(request: Request) -> str | None:
    """Токен текущей сессии для подстановки в ссылки; None у веб-ветки.

    None означает «личность несёт cookie, дописывать к адресу нечего».
    """
    return getattr(request.state, _STATE_TOKEN, None)


def with_session(request: Request, path: str) -> str:
    """Внутренний адрес с токеном сессии в query; у веб-ветки — без изменений.

    Через неё обязаны проходить ВСЕ внутренние адреса — ссылки, `action`
    форм, `Location` редиректов: в VK токен и есть личность, и адрес без
    него отдаёт 403. Алфавит JWT (base64url и точки) в query безопасен,
    поэтому экранирования нет.
    """
    token = session_token(request)
    if token is None:
        return path
    sep = "&" if "?" in path else "?"
    return f"{path}{sep}{SESSION_QUERY_PARAM}={token}"


def in_vk(request: Request) -> bool:
    """Показывается ли страница внутри VK: есть ли в адресе `s` или `sign`.

    Решает, рисовать ли блоки про оплату: продавать внутри Mini App нельзя ни
    на одной платформе (docs/astro-context.md). Личность НЕ проверяет — это
    выбор вёрстки, а не охрана доступа, и ошибиться он может только в сторону
    «спрятать». Различать iOS, Android и веб не нужно: запрет общий.
    """
    q = request.query_params
    return SESSION_QUERY_PARAM in q or auth.VK_SIGN_PARAM in q


def forbid_in_vk(request: Request) -> None:
    """Зависимость страниц оплаты: пришедшему из VK — 403 вместо витрины.

    Ссылок на оплату в VK-ветке не осталось, но адрес угадывается и открыть
    его можно руками; витрина с ценой внутри Mini App — прямое нарушение
    5.4.1. Вебхуку ЮKassa зависимость не нужна: он приходит с чужого сервера
    и `?s=` не несёт.
    """
    if in_vk(request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Оплата доступна только на сайте, вне приложения VK",
        )


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
