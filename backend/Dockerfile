# Образ backend (FastAPI + расчётное ядро).
#
# ВАЖНО: контекст сборки — КОРЕНЬ репозитория, не backend/:
#   docker build -f backend/Dockerfile .
# Причина — uv workspace: `uv.lock` и корневой `pyproject.toml` лежат выше
# backend/, а Docker не умеет читать файлы вне контекста.

# --- Стадия 1: собираем окружение -------------------------------------------
# Официальный образ uv поверх того же python:3.13-slim-bookworm, что и рантайм
# ниже, — venv из этой стадии ссылается на интерпретатор по тому же пути и
# в финальном образе работает как родной.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

# COMPILE_BYTECODE — .pyc пишутся при сборке, а не при первом запросе.
# LINK_MODE=copy — без него uv пытается делать хардлинки из кеша в /app и
# ругается: это разные файловые системы (кеш примонтирован).
# PYTHON_DOWNLOADS=never — брать питон из образа, а не качать свой.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# pyswisseph (сам Swiss Ephemeris) готового wheel под cp313 не публикует и
# собирается из C-исходников — без gcc установка падает. Компилятор нужен
# только здесь: в рантайм-образ эта стадия не копируется.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Сначала только описания зависимостей: слой с установкой кешируется и не
# пересобирается при каждой правке кода. bot/pyproject.toml нужен, хотя бот в
# образ не едет, — без файла члена workspace uv не разрешит `uv.lock`.
COPY pyproject.toml uv.lock ./
COPY backend/pyproject.toml backend/
COPY bot/pyproject.toml bot/

# --frozen: если lock разошёлся с pyproject, сборка падает. Молча ставить
# другие версии нельзя — в образе окажется не то, что проверено локально.
# --no-install-workspace: ставим ТОЛЬКО сторонние пакеты, наш код ещё не скопирован.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace --package astro-backend

# Код — последним слоем, ограничен .dockerignore в корне репозитория.
COPY backend/ backend/

# Второй проход доставляет сам astro-backend (editable: .pth-ссылка на
# /app/backend, который в образе и лежит) — так `import app` работает,
# а исходники существуют в одном экземпляре.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package astro-backend

# --- Стадия 2: рантайм ------------------------------------------------------
FROM python:3.13-slim-bookworm

# В slim-образе нет базы таймзон: ZoneInfo("Europe/Moscow") падает с
# ZoneInfoNotFoundError, а на ней держится весь расчёт натальной карты.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

# Логи в docker logs сразу, без буферизации stdout.
ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# Процесс не от root: пробой в вебе не должен давать права на файлы образа.
RUN useradd --create-home --uid 10001 app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/backend /app/backend

USER app
# Из backend/ запускается и alembic (здесь alembic.ini), и uvicorn.
WORKDIR /app/backend

EXPOSE 8000

# JSON-форма: uvicorn становится PID 1 и получает SIGTERM от docker stop
# напрямую, без прослойки shell.
#
# Воркер ровно один (умолчание uvicorn) и это не оптимизация, а требование:
# замок генерации в services/reports.py и реестр в web/generation.py живут в
# памяти процесса. Второй воркер их не видит — тот же разбор уйдёт в DeepSeek
# дважды, то есть за один текст заплатим два раза.
#
# --proxy-headers: снаружи стоит Caddy, без этого приложение считает клиентом
# его. --forwarded-allow-ips=* безопасен ровно потому, что порт 8000 наружу не
# опубликован: достучаться до него можно только из сети compose.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
