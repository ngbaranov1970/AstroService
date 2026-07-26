# Та же мажорная версия Python, что в локальном .venv (3.13) — иначе
# поведение зависимостей может отличаться от проверенного локально.
FROM python:3.13-slim

# В slim-образе нет базы таймзон: без tzdata ZoneInfo("Europe/Moscow")
# из config.py падает с ZoneInfoNotFoundError при старте.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

# Логи в docker logs сразу, без буферизации stdout.
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Сначала только lock-файл: слой с pip install кешируется и не
# пересобирается при правках кода — только при смене зависимостей.
COPY requirements.lock.txt .
RUN pip install --no-cache-dir -r requirements.lock.txt

# Код — последним слоем; что именно копируется, ограничивает .dockerignore.
COPY . .

# JSON-форма: python становится PID 1 и получает SIGTERM от docker stop
# напрямую, без прослойки shell — бот успевает корректно завершиться.
CMD ["python", "main.py"]
