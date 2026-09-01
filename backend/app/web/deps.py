"""Зависимости Jinja-страниц: внутри ли мы VK и охрана админки.

Опознания пользователя здесь больше нет — оно ушло вместе со стендом
(docs/astro-context.md, «Стенд снят, адреса разведены»). Оставшиеся страницы
личности не спрашивают: оплата опознаёт по токену карты, юридические открыты
всем, админка закрыта паролем. Кто пришёл в приложение, решает app/api/deps.py
по заголовку `Authorization: Bearer`.

Отсюда и состав файла: `in_vk` — выбор вёрстки, `forbid_in_vk` — охрана
витрины с ценой, `require_admin` — охрана админки.
"""

from __future__ import annotations

import logging
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import get_settings
from app.services import auth

logger = logging.getLogger(__name__)

# Имя параметра с сессионным токеном в адресе. Своих ссылок с ним не осталось,
# но признаком «запрос пришёл из VK» он служит по-прежнему: в Mini App личность
# едет в адресе, и параметр виден на любой странице, открытой изнутри.
SESSION_QUERY_PARAM = "s"


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


# --- Админка ---------------------------------------------------------------

# auto_error=True: без заголовка авторизации FastAPI сам ответит 401 с
# WWW-Authenticate, и браузер покажет окно логина и пароля.
_basic = HTTPBasic(auto_error=True)


def require_admin(
    credentials: Annotated[HTTPBasicCredentials, Depends(_basic)],
) -> str:
    """HTTP Basic на /admin. Возвращает имя вошедшего, иначе бросает 401 или 503.

    За страницей — имена, даты и места рождения живых людей, а сервер по
    определению в открытом интернете. Basic хватает на инструмент для двух
    человек, но пароль идёт почти открытым текстом: обязателен HTTPS (Caddy).
    Пароль не задан → 503, а не «пускаем всех».
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
