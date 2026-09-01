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

from app.api import api_router
from app.config import get_settings
from app.db import dispose_engine
from app.llm import client as llm_client
from app.logging_setup import setup_logging
from app.payments import yookassa
from app.session_cookie import install_session_cookie
from app.vk import api as vk_api
from app.web.routes.admin import router as admin_router
from app.web.routes.legal import router as legal_router
from app.web.routes.pay import router as pay_router
from app.web.routes.redirects import router as redirects_router
from app.web.routes.vk_callback import router as vk_callback_router

logger = logging.getLogger(__name__)

# Папка со статикой стенда (один style.css). Путь считаем от этого файла,
# а не от текущей директории: приложение должно одинаково запускаться и из
# backend/, и из корня репозитория, и из контейнера.
STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"

# Собранный Vue-фронт (npm run build). В образе его кладёт сюда же стадия
# frontend из backend/Dockerfile, поэтому путь один на оба случая.
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend" / "dist"


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
    # HTTP-клиенты держат открытые TCP/TLS-соединения к DeepSeek и ЮKassa.
    # Оба закрываются молча, если ни одного запроса не было.
    await llm_client.close_client()
    await yookassa.close_client()
    await vk_api.close_client()


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

    # Статика Jinja-страниц (оплата, юридические, админка). Монтируется на
    # приложение целиком, поэтому живёт здесь, а не внутри web/.
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # Vue-фронт на /app/. Монтируем, только если сборка есть: при локальном
    # запуске без npm run build каталога нет, и StaticFiles упал бы на старте.
    # html=True отдаёт index.html на /app/ — адреса экранов живут после «#»
    # (createWebHashHistory), сервер их не видит, и SPA-fallback не нужен.
    if FRONTEND_DIR.is_dir():
        app.mount("/app", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
    else:
        logger.warning("Сборки фронта нет (%s) — /app отдаваться не будет", FRONTEND_DIR)

    # Cookie-личность для входа по /api/v1/auth/web. Маршрут работает только
    # вне прода, но middleware ставится всегда: включать его по окружению
    # значит отлаживать в разработке не то, что поедет в бой.
    install_session_cookie(app)
    # Корень и старые ссылки /r/<token> — на Vue. Стоит до остальных роутеров:
    # маршруты конкретнее, чем «всё остальное», и порядок здесь читаемости
    # ради, пересечься им не с чем.
    app.include_router(redirects_router)
    app.include_router(admin_router)
    # Страницы оплаты и вебхук ЮKassa. Своей авторизации у них нет
    # намеренно: платят из внешнего браузера, где нашей cookie не бывает.
    app.include_router(pay_router)
    # События сообщества VK (кто разрешил и кто запретил писать). Стоит рядом
    # с оплатой по той же причине: приходит с чужого сервера, нашей cookie
    # там нет, подлинность проверяется общим секретом внутри самого роутера.
    app.include_router(vk_callback_router)
    # Оферта, цены и реквизиты. Подключать обязательно, даже если стенд
    # выключат: импорт этого модуля кладёт `merchant` в шаблоны, а он
    # печатается в подвале КАЖДОЙ страницы.
    app.include_router(legal_router)

    # JSON-API для Mini App. Идёт после стенда, но пересечься они не могут:
    # у API свой префикс /api/v1 и своя авторизация по заголовку.
    app.include_router(api_router)

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
