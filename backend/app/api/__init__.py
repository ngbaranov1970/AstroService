"""JSON-API для фронта: сборка всех роутеров под одним префиксом.

Версия в пути (`/api/v1`) стоит с самого начала: собранный Mini App живёт в
кеше у VK и на устройствах, и сломать форму ответов, не уронив старые
сборки, иначе будет нечем.

Здесь только сборка — маршруты лежат в app/api/routes/, вспомогательное
(зависимости) — рядом с этим файлом.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import auth, charts, reports

API_PREFIX = "/api/v1"

api_router = APIRouter(prefix=API_PREFIX)
api_router.include_router(auth.router)
api_router.include_router(charts.router)
api_router.include_router(reports.router)
