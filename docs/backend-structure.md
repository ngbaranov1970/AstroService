# Структура backend

Описание разложения кода `/backend` и обоснование ключевых решений. Читать вместе с [astro-project-plan.md](astro-project-plan.md) (модель данных, API, план по неделям).

```
backend/
├── pyproject.toml
├── alembic/
│   └── versions/
├── astro_core/
│   ├── __init__.py
│   ├── natal.py
│   ├── wheel.py
│   ├── matrix.py
│   ├── eastern.py
│   ├── geo.py
│   └── passport.py
├── app/
│   ├── main.py
│   ├── config.py
│   ├── db.py
│   ├── models.py
│   ├── schemas/
│   ├── api/
│   │   ├── auth.py
│   │   ├── charts.py
│   │   ├── reports.py
│   │   ├── orders.py
│   │   └── webhooks.py
│   ├── services/
│   │   ├── auth.py
│   │   ├── charts.py
│   │   ├── reports.py
│   │   ├── orders.py
│   │   └── events.py
│   └── llm/
│       ├── client.py
│       └── prompts/
└── tests/
    ├── golden/
    └── test_natal.py
```

## Почему так

### `astro_core/` — расчётное ядро, отдельно от `app/`

Ядро не импортирует ничего из `app` (ни FastAPI, ни базу, ни VK). Вход — дата/время/координаты/tz, выход — dict «астропаспорта». Это прямое следствие требования из CLAUDE.md: **ядро платформо-независимое**. Такое разделение даёт три вещи:

- ядро без изменений переиспользует Telegram-бот и любой будущий фронт;
- golden-тесты (сверка с astro-seek) гоняются без поднятия FastAPI и базы;
- расчёт можно будет вынести в отдельный сервис/пакет, если понадобится, без переписывания.

Файлы:
- **natal.py** — pyswisseph + kerykeion: планеты, дома (Placidus), аспекты с орбисами
- **wheel.py** — обёртка над kerykeion для SVG-колеса
- **matrix.py** — Матрица Судьбы, чистая арифметика по дате рождения
- **eastern.py** — восточный гороскоп (cnlunar/lunardate)
- **geo.py** — геокодинг (GeoNames) + timezonefinder; **историческую таймзону считать по координатам И дате рождения** — частая ошибка конкурентов, отдельно оговорено в CLAUDE.md
- **passport.py** — сборка единого JSON «астропаспорта» из результатов natal/matrix/eastern — этот JSON идёт и в LLM, и на фронт, и хранится в `charts.calc`

### `app/` — веб-слой

**Роутеры (`api/`) тонкие**, вся логика — в `services/`. Ручек всего шесть (см. план проекта), но, например, в `reports` уже сходятся кеш по (расчёт, тип, prompt_version), лимит бесплатных генераций на пользователя, статусы генерации и связь с оплатой — этому нужен отдельный модуль, а не код в роутере.

- **api/auth.py** → `POST /api/auth/vk`
- **api/charts.py** → `POST /api/charts`, `GET /api/charts/{id}`
- **api/reports.py** → `POST /api/reports/{type}`
- **api/orders.py** → `POST /api/orders`
- **api/webhooks.py** → `POST /api/webhooks/yookassa`

**services/**
- **auth.py** — проверка HMAC-SHA256 `sign` из launch params VK → выдача JWT
- **charts.py** — геокодинг → вызов astro_core → сохранение `calc` JSONB
- **reports.py** — кеш отчётов, лимит бесплатных генераций, статусы (`pending/ready/failed`), вызов LLM
- **orders.py** — создание платежа в ЮKassa, обработка вебхука → разблокировка отчёта
- **events.py** — запись событий воронки в таблицу `events`

**models.py** — все 5 таблиц (`users`, `charts`, `reports`, `orders`, `events`) одним файлом: на MVP пять таблиц не заслуживают отдельного пакета, разрежем на модули, когда станет тесно.

**llm/**
- **client.py** — клиент DeepSeek
- **prompts/** — промпты файлами с версией в имени (`free_summary_v1.md`, `full_natal_v1.md`, ...); `prompt_version` в таблице `reports` — это имя файла. Кеш-ключ отчёта = (calc JSONB, type, prompt_version) — уникальный индекс в базе даёт кеширование «бесплатно», через `SELECT` перед генерацией

### Генерация платных отчётов — без очереди

DeepSeek на полном разборе думает десятки секунд — держать HTTP-запрос столько нельзя, особенно в вебхуке ЮKassa (должен отвечать быстро). На MVP: `BackgroundTasks` FastAPI + поле `status` в `reports`, фронт поллит `GET /api/reports/{id}`. Очередь (arq/Celery + Redis) — отложена до v1.1, когда появятся рассылки по расписанию из плана месяца 2.

### tests/golden/

Эталонные даты для сверки с astro-seek (10 карт) — обязательный тест **до написания любого другого кода**, согласно жёстким ограничениям в CLAUDE.md. Ошибка в домах убивает доверие мгновенно.

## Открытые вопросы (не решено окончательно)

- **GeoNames**: таблица в Postgres с триграммным индексом vs. отдельный SQLite-файл рядом с ядром. Предпочтение — таблица в Postgres (`cities500` для RU/UA/BY/KZ + крупные мировые), проще для автокомплита городов в онбординге.
- **sync vs async SQLAlchemy**: предпочтение — async (asyncpg), FastAPI-нативно. pyswisseph синхронный CPU-bound, но расчёт занимает миллисекунды — не проблема, оборачивать в `run_in_executor` не обязательно.
- **alembic**: директория создана заранее (`alembic/versions/`), но реальный `alembic.ini` и `env.py` появятся при первом `alembic init` — на данном этапе это просто зарезервированное место в дереве.
