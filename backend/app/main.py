"""Точка входа приложения: собирает FastAPI из готовых кусков.

Здесь намеренно нет никакой логики — ни расчётов, ни работы с базой.
Файл отвечает на три вопроса: что подключено, что происходит при старте
и остановке, и где лежит статика. Всё остальное живёт в своих модулях.

Запуск для разработки:
    uv run uvicorn app.main:app --reload
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.db import dispose_engine
from app.logging_setup import setup_logging
from app.web.admin import router as admin_router
from app.web.deps import install_session_cookie
from app.web.router import router as web_router

logger = logging.getLogger(__name__)

# Папка со статикой стенда (один style.css). Путь считаем от этого файла,
# а не от текущей директории: приложение должно одинаково запускаться и из
# backend/, и из корня репозитория, и из контейнера.
STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Что делаем при старте и остановке приложения.

    Код до `yield` — старт, после `yield` — остановка. Это современная
    замена устаревшим декораторам `@app.on_event("startup"/"shutdown")`.

    При старте делать нечего: движок базы создаётся лениво, при первом
    запросе (см. `db.get_engine`). Это сознательно — так приложение
    поднимается даже когда Postgres ещё не готов (частый случай в
    docker compose, где контейнеры стартуют одновременно).

    Схему базы здесь НЕ создаём: таблицы накатывает Alembic. Если
    приложение начнёт создавать их само, оно рано или поздно разойдётся с
    миграциями, и `alembic check` перестанет что-либо означать.
    """
    yield
    # Остановка: закрыть все соединения пула. Без этого asyncpg пишет в лог
    # жалобы на соединения, брошенные при закрытии event loop.
    logger.info("Остановка: закрываю пул соединений с базой")
    await dispose_engine()


def create_app() -> FastAPI:
    """Собрать приложение.

    Отдельная функция, а не код на уровне модуля, потому что тестам удобно
    получать чистый экземпляр приложения на каждый прогон.
    """
    # Первым делом — логи: всё, что случится дальше при сборке приложения,
    # должно уже быть видно.
    setup_logging()
    settings = get_settings()
    logger.info("Запуск AstroService: env=%s, debug=%s", settings.app_env, settings.debug)

    app = FastAPI(
        title="AstroService",
        version="0.1.0",
        lifespan=lifespan,
        # Автодокументацию наружу отдаём только в разработке: в проде она
        # показывает всем желающим полный список наших ручек.
        docs_url="/docs" if settings.debug else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.debug else None,
    )

    # Статика стенда. Монтируется на приложение целиком, поэтому живёт
    # здесь, а не внутри web/.
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # Веб-стенд: cookie-сессия обязана стоять до роутера — по ней deps.py
    # узнаёт, кто пришёл, а без неё каждый запрос был бы новым человеком.
    install_session_cookie(app)
    app.include_router(web_router)
    app.include_router(admin_router)

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        """Жив ли процесс. Смотрят Caddy и docker compose, не человек.

        Намеренно НЕ ходит в базу: проверка живости должна отвечать быстро
        и не падать вместе с Postgres — иначе перезапуск базы утащит за
        собой и приложение, которому достаточно было подождать.
        """
        return {"status": "ok"}

    return app


app = create_app()
