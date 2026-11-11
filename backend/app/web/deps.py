"""Зависимости Jinja-страниц: внутри ли мы VK и охрана админки.

Опознания пользователя здесь больше нет — оно ушло вместе со стендом
(docs/astro-context.md, «Стенд снят, адреса разведены»). Оставшиеся страницы
личности не спрашивают: оплата опознаёт по токену карты, юридические открыты
всем, админка закрыта паролем. Кто пришёл в приложение, решает app/api/deps.py
по заголовку `Authorization: Bearer`.

Отсюда и состав файла: `in_vk` — выбор вёрстки, `forbid_in_vk` — охрана
витрины с ценой, `require_admin` — охрана админки
(cookie-сессия, запасной вход — HTTP Basic).
"""

from __future__ import annotations

import logging
import secrets
from urllib.parse import urlencode

from fastapi import HTTPException, Request, status
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

# Cookie с сессией админки (auth.issue_admin_session). Ставит и снимает её
# routes/admin_login.py.
ADMIN_COOKIE = "astro_admin"

# Basic оставлен запасным входом: curl и браузеры, где пароль уже сохранён.
# auto_error=False — без заголовка вернёт None, а не 401: такого гостя мы
# отправляем на форму входа.
_basic = HTTPBasic(auto_error=False)


def admin_password_set() -> bool:
    """Задан ли ADMIN_PASSWORD. Нет — админка закрыта для всех (503)."""
    if get_settings().admin_password is None:
        logger.error("ADMIN_PASSWORD не задан — админка закрыта (503)")
        return False
    return True


def admin_credentials_ok(username: str, password: str) -> bool:
    """Сверить логин и пароль админки с настройками за постоянное время."""
    settings = get_settings()
    if settings.admin_password is None:
        return False
    # Оба сравнения выполняются всегда (не через `and`, который выйдет
    # раньше): иначе по времени ответа видно, угадано ли имя. Байты, а не
    # строки, — compare_digest не принимает не-ASCII.
    user_ok = secrets.compare_digest(
        username.encode("utf-8"), settings.admin_user.encode("utf-8")
    )
    password_ok = secrets.compare_digest(
        password.encode("utf-8"),
        settings.admin_password.get_secret_value().encode("utf-8"),
    )
    return user_ok and password_ok


async def require_admin(request: Request) -> str:
    """Охрана /admin: cookie-сессия, иначе HTTP Basic. Возвращает имя вошедшего.

    Без входа GET уводит на /admin/login (303) — окно Basic встроенные
    браузеры и webview не показывают. Остальные методы получают 401.
    Пароль не задан → 503, а не «пускаем всех».
    """
    if not admin_password_set():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Админка не настроена",
        )

    token = request.cookies.get(ADMIN_COOKIE)
    if token:
        username = auth.read_admin_session(token)
        if username is not None:
            return username

    credentials: HTTPBasicCredentials | None = await _basic(request)
    if credentials is not None:
        if admin_credentials_ok(credentials.username, credentials.password):
            return credentials.username
        logger.warning("Неудачный вход в админку")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль",
            # Без этого заголовка браузер не покажет окно повторного ввода.
            headers={"WWW-Authenticate": "Basic"},
        )

    if request.method in ("GET", "HEAD"):
        target = request.url.path
        if request.url.query:
            target += f"?{request.url.query}"
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": f"/admin/login?{urlencode({'next': target})}"},
        )
    # Без WWW-Authenticate: браузер не должен открывать окно Basic поверх формы.
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Нужен вход в админку")
