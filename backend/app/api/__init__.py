"""JSON-API для фронта: сборка всех роутеров под одним префиксом.

Версия в пути (`/api/v1`) стоит с самого начала: собранный Mini App живёт в
кеше у VK и на устройствах, и сломать форму ответов, не уронив старые
сборки, иначе будет нечем.

Здесь только сборка — маршруты лежат в соседних модулях.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api import auth

API_PREFIX = "/api/v1"

api_router = APIRouter(prefix=API_PREFIX)
api_router.include_router(auth.router)
