"""Зависимости JSON-API: опознание пользователя по токену.

Сессия базы тут только переобъявлена алиасом `SessionDep` — открывает её и
откатывает `get_session` из app/db.py.

Граница с app/web/deps.py проходит по способу опознания. Там личность несёт
cookie (`platform='web'`) или адрес (`?s=` внутри VK), потому что браузер
ходит по ссылкам и формам, которые мы же и рисуем. Здесь ходит fetch из
Vue — заголовок ставит клиент, и токен едет в `Authorization: Bearer`, не
оседая в логах Caddy вместе с URL.

Второе отличие: тут никто не заводится сам. Единственная точка входа —
POST /auth/vk (app/api/routes/auth.py); незнакомый токен получает 401, а
не новую строку в users.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Chart, User
from app.services import auth
from app.services import charts as charts_service
from app.services import users as users_service

# auto_error=False — иначе FastAPI сам ответит 403 на запрос без заголовка,
# а нам нужен 401: по нему фронт понимает «сходи за токеном в /auth/vk»,
# тогда как 403 означает «ты опознан, но сюда нельзя».
_bearer = HTTPBearer(auto_error=False)

SessionDep = Annotated[AsyncSession, Depends(get_session)]

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Сессия недействительна — откройте приложение заново",
    # Требование RFC 7235 к ответу 401; без него заголовок ответа неполный,
    # и некоторые HTTP-клиенты не отличают его от 403.
    headers={"WWW-Authenticate": "Bearer"},
)


async def current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: SessionDep,
) -> User:
    """Пользователь по нашему JWT из заголовка; 401 на любую неудачу.

    Неудача — это отсутствующий заголовок, просроченный или подделанный
    токен и удалённый пользователь. Различать их в ответе не нужно: фронту
    во всех случаях делать одно и то же — переавторизоваться параметрами
    запуска, которые VK держит в адресе Mini App.
    """
    if credentials is None:
        raise _UNAUTHORIZED

    user_id = auth.read_session(credentials.credentials)
    if user_id is None:
        raise _UNAUTHORIZED

    # Подпись остаётся верной и после удаления пользователя — сам токен о
    # строке в базе ничего не знает, поэтому проверяем и её.
    user = await users_service.get_by_id(session, user_id)
    if user is None:
        raise _UNAUTHORIZED

    return user


CurrentUser = Annotated[User, Depends(current_user)]


async def chart_by_token(token: str, _user: CurrentUser, session: SessionDep) -> Chart:
    """Карта по токену из пути; 404, если такой нет.

    Пользователя требует ради порядка: без него FastAPI решал бы зависимости
    в порядке объявления аргументов маршрута, и запрос без токена сначала
    искал бы карту в базе, а 401 получал бы после — то есть неопознанный
    клиент и базу дёргал, и по коду ответа узнавал, существует ли карта.

    Проверки владельца здесь нет: токен и есть ключ, 24 случайных байта не
    подобрать (см. services/charts.py). Кто знает ссылку — видит карту;
    ограничивают доступ маршруты, которые тратят лимиты и деньги владельца.
    """
    chart = await charts_service.get_chart_by_token(session, token)
    if chart is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Такой карты нет")
    return chart


ChartByToken = Annotated[Chart, Depends(chart_by_token)]
