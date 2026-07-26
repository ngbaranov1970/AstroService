"""Подключение к Postgres: движок, фабрика сессий, базовый класс моделей.

Здесь нет ни одной таблицы — только инфраструктура. Таблицы описаны
в app/models.py, они наследуются от Base отсюда.

Асинхронный стек (SQLAlchemy 2 + asyncpg) выбран под FastAPI: обработчик
запроса и так ждёт сеть — DeepSeek отвечает десятками секунд, — и держать
на каждом таком ожидании поток из синхронного пула расточительно.

Четыре решения, которые стоит понимать, потому что каждое лечит конкретную
ошибку времени выполнения:

1. Движок и фабрика сессий создаются лениво (get_engine / get_sessionmaker),
   а не как переменные уровня модуля. Тот же приём и по той же причине, что
   в app/config.py: `engine = create_async_engine(...)` в теле модуля означает,
   что любой импорт app.db читает настройки, а значит требует заполненного
   .env — включая pytest и alembic. Плюс asyncpg-пул, созданный при импорте,
   привязывается к тому event loop, который активен в этот момент; в тестах
   это регулярно оказывается уже закрытый loop.

2. expire_on_commit=False. По умолчанию SQLAlchemy после commit() помечает
   все атрибуты объектов как «протухшие», и следующее обращение к
   chart.calc молча идёт в базу за свежим значением. В async-режиме такая
   неявная догрузка запрещена и падает с MissingGreenlet — причём уже после
   того, как транзакция закоммичена, то есть в самом неудобном месте.
   С False объект после коммита остаётся обычным Python-объектом с данными.

3. pool_pre_ping=True. Соединение в пуле может умереть незаметно: рестарт
   Postgres, таймаут на стороне сервера, разрыв у облачного провайдера.
   Без пинга это выстреливает случайной ошибкой в первом же запросе после
   простоя; с пингом SQLAlchemy проверяет соединение перед выдачей и молча
   заменяет мёртвое.

4. Соглашение об именах ограничений (naming_convention). Postgres сам
   придумывает имена индексам и внешним ключам, и они отличаются от того,
   что генерирует Alembic. Как только понадобится миграция, снимающая
   ограничение, autogenerate напишет DROP CONSTRAINT с именем, которого в
   базе нет. Явное соглашение делает имена предсказуемыми во всех средах.
   Задавать его надо ДО первой миграции — потом придётся переименовывать
   существующие ограничения руками.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

# ix — индекс, uq — уникальность, ck — check, fk — внешний ключ, pk — первичный.
# %(column_0_N_name)s подставляет имена столбцов, поэтому составной индекс
# получает осмысленное имя вида ix_reports_chart_id_type.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Общий предок всех моделей.

    Его metadata — это и есть «схема, как её видит код»: Alembic сравнивает
    её с реальной базой и по разнице пишет миграции. Поэтому каждый модуль
    с таблицами обязан быть импортирован до сравнения (см. alembic/env.py),
    иначе незамеченная таблица выглядит как удалённая.
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


@lru_cache
def get_engine() -> AsyncEngine:
    """Движок — один на процесс (в нём живёт пул соединений).

    lru_cache здесь не оптимизация, а требование корректности: два движка
    означают два независимых пула, то есть удвоенное число соединений
    к Postgres при том же трафике.
    """
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        echo=settings.db_echo,
        pool_pre_ping=True,
        # 5 постоянных соединений + 10 временных на всплеск. Ориентир —
        # дефолтный лимит Postgres в 100 соединений: с запасом на несколько
        # воркеров uvicorn, alembic и psql под рукой.
        pool_size=5,
        max_overflow=10,
    )


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Фабрика сессий. Сессия — одна единица работы, обычно один HTTP-запрос."""
    return async_sessionmaker(
        bind=get_engine(),
        expire_on_commit=False,
        autoflush=True,
    )


async def get_session() -> AsyncIterator[AsyncSession]:
    """Зависимость FastAPI: `session: AsyncSession = Depends(get_session)`.

    Транзакцией управляет вызывающий код: сессия сама ничего не коммитит.
    Причина — обработчик должен решать, что считать успехом; неявный коммит
    в зависимости сохранил бы половину работы там, где ошибка случилась
    после записи в базу, но до ответа клиенту.

    Откат при исключении здесь всё же есть: соединение возвращается в пул
    с активной транзакцией, если её не закрыть, и следующий, кто его
    получит, унаследует чужую незавершённую работу.
    """
    async with get_sessionmaker()() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    """Закрыть все соединения пула — вызывается на остановке приложения.

    Без этого asyncpg жалуется в лог на соединения, брошенные при закрытии
    event loop, а Postgres какое-то время держит их открытыми.
    """
    if get_engine.cache_info().currsize:
        await get_engine().dispose()
