"""Редирект со старой ссылки на разбор.

Стенд снят (docs/astro-context.md, «Стенд снят, адреса разведены»), но его
ссылка `/r/<token>` пережить снос обязана: она уже роздана.

Корень домена жил здесь же, пока отвечал редиректом. Теперь там страница —
см. routes/landing.py.
"""

from __future__ import annotations

from fastapi import APIRouter, status
from fastapi.responses import RedirectResponse

from app.web.routes.landing import APP_PATH

router = APIRouter(include_in_schema=False)


@router.get("/r/{token}")
async def report_page(token: str) -> RedirectResponse:
    """Старая ссылка на разбор — на экран карты в приложении.

    Ссылка пересылаема и уже роздана: она уходила в разборах и в письмах
    рассылки, вернуть её нельзя. Адрес экрана живёт после «#», сервер эту
    часть не увидит — её разберёт роутер Vue уже в браузере.
    """
    return RedirectResponse(f"{APP_PATH}#/chart/{token}", status.HTTP_301_MOVED_PERMANENTLY)
