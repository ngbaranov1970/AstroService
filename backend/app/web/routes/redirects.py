"""Редиректы со старых адресов стенда на Vue-фронт.

Стенд снят (docs/astro-context.md, «Стенд снят, адреса разведены»), но два
его адреса пережить снос обязаны: корень домена и ссылка на разбор.

КОДЫ ОТВЕТА РАЗНЫЕ, И ЭТО НАМЕРЕННО

`/r/<token>` уезжает навсегда — 301. Корень отвечает 302, потому что там
появится лендинг: 301 браузеры кешируют до очистки истории, и посетители,
однажды зашедшие на корень, лендинга не увидели бы никогда.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.responses import RedirectResponse

router = APIRouter(include_in_schema=False)

# Тот же путь, что `base` в frontend/vite.config.js и APP_BASE_PATH в pay.py.
APP_PATH = "/app/"


@router.get("/")
async def index(request: Request) -> RedirectResponse:
    """Корень домена — пока в приложение, потом на лендинг.

    Временный редирект: 302 сменится на страницу, а не на другой адрес.
    """
    # Всё, что стояло после «?», переносим на новый адрес. В VK личность едет
    # именно там (sign и vk_*): если приложение в кабинете указано на корень,
    # редирект без параметров оставил бы Mini App без запуска вовсе.
    query = request.url.query
    return RedirectResponse(
        f"{APP_PATH}?{query}" if query else APP_PATH,
        status.HTTP_302_FOUND,
    )


@router.get("/r/{token}")
async def report_page(token: str) -> RedirectResponse:
    """Старая ссылка на разбор — на экран карты в приложении.

    Ссылка пересылаема и уже роздана: она уходила в разборах и в письмах
    рассылки, вернуть её нельзя. Адрес экрана живёт после «#», сервер эту
    часть не увидит — её разберёт роутер Vue уже в браузере.
    """
    return RedirectResponse(f"{APP_PATH}#/chart/{token}", status.HTTP_301_MOVED_PERMANENTLY)
