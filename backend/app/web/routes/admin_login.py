"""Вход в админку формой и выход: ставит и снимает cookie-сессию.

Отдельный роутер, а не часть routes/admin.py: на том висит require_admin,
и форма входа оказалась бы за паролем. Проверку на остальных страницах
делает web/deps.require_admin.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from starlette.responses import Response

from app.services import auth
from app.web.deps import ADMIN_COOKIE, admin_credentials_ok, admin_password_set
from app.web.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", include_in_schema=False)

# Пауза перед ответом на неверный пароль. Перебор по сети замедляет в разы,
# а владелец, ошибившийся раз, её не заметит.
_FAIL_DELAY = 1.0


def _safe_next(raw: str | None) -> str:
    """Куда вернуть после входа: только путь внутри /admin, иначе главная админки.

    Без проверки ?next=https://чужой.сайт превращал бы нашу форму входа в
    переадресацию куда угодно — готовая приманка для фишинга.
    """
    if (
        not raw
        or not raw.startswith("/admin")
        or raw.startswith(("//", "/admin/login"))
        or "\\" in raw
    ):
        return "/admin"
    return raw


def _ensure_configured() -> None:
    if not admin_password_set():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Админка не настроена",
        )


@router.get("/login")
async def login_page(
    request: Request, next_: Annotated[str | None, Query(alias="next")] = None
) -> Response:
    _ensure_configured()
    return templates.TemplateResponse(
        request, "admin_login.html", {"next": _safe_next(next_), "error": None}
    )


@router.post("/login")
async def login(
    request: Request,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    next_: Annotated[str | None, Form(alias="next")] = None,
) -> Response:
    _ensure_configured()
    target = _safe_next(next_)
    if not admin_credentials_ok(username, password):
        logger.warning("Неудачный вход в админку через форму")
        await asyncio.sleep(_FAIL_DELAY)
        return templates.TemplateResponse(
            request,
            "admin_login.html",
            {"next": target, "error": "Неверный логин или пароль"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    response = RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        ADMIN_COOKIE,
        auth.issue_admin_session(username),
        max_age=auth.ADMIN_SESSION_TTL,
        path="/admin",
        # Lax, а не Strict: ссылка на карточку заказа из уведомления в VK должна
        # открываться уже вошедшим. Чужой сайт POST от нашего имени всё равно
        # не отправит — Lax не несёт cookie в межсайтовых POST.
        samesite="lax",
        httponly=True,
        secure=True,
    )
    return response


@router.post("/logout")
async def logout() -> Response:
    response = RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(ADMIN_COOKIE, path="/admin", samesite="lax", httponly=True, secure=True)
    return response
