"""Корень домена: лендинг или запуск Mini App.

Один маршрут с развилкой. Личности не спрашивает и строку в `users` не
заводит — как и юридические страницы: заход поисковика или проверяющего не
должен попадать в воронку.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.responses import RedirectResponse
from starlette.responses import Response

from app.llm.prompts import full_natal
from app.web.templating import templates

# Тот же путь, что `base` в frontend/vite.config.js и APP_BASE_PATH в pay.py.
APP_PATH = "/app/"

router = APIRouter(include_in_schema=False)


def _is_vk_launch(params) -> bool:
    """Пришёл ли запрос запуском Mini App, а не обычным заходом в браузере.

    VK кладёт в адрес подпись `sign` и набор `vk_*`; хватает любого из них.
    """
    return "sign" in params or any(key.startswith("vk_") for key in params)


@router.get("/")
async def index(request: Request) -> Response:
    """Лендинг, а для запуска из VK — переход в приложение.

    Развилка обязательна: если приложение в кабинете VK указано на корень, то
    launch params приходят сюда, и показать им лендинг значит не запустить
    Mini App вообще.
    """
    if _is_vk_launch(request.query_params):
        # Query переносим целиком: в нём личность (sign и vk_*), и потеря
        # хотя бы части параметров ломает проверку подписи на стороне фронта.
        return RedirectResponse(f"{APP_PATH}?{request.url.query}", status.HTTP_302_FOUND)

    return templates.TemplateResponse(
        request,
        "landing.html",
        {
            "app_path": APP_PATH,
            # Состав полного разбора берём у промпта, а не переписываем в
            # шаблон: разойдись они — на витрине обещаны не те части, которые
            # человек получит.
            "full_sections": full_natal.SECTION_TITLES,
        },
    )
