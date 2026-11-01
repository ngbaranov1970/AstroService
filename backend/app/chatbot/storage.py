"""Чтение и запись состояния диалога; идемпотентность Callback-событий.

Сессию не коммитит — как app/services/*: транзакцию закрывает вызывающий.
Про VK и HTTP не знает: только user_id и event_id.
"""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BotDialog, BotEventSeen


async def load(session: AsyncSession, user_id: int) -> BotDialog:
    """Вернуть строку диалога; если ещё нет — создать idle с пустым data."""
    dialog = await session.get(BotDialog, user_id)
    if dialog is not None:
        return dialog

    dialog = BotDialog(user_id=user_id)
    session.add(dialog)
    # flush: у PK нет автоинкремента, но соседние INSERT в той же транзакции
    # уже могут ссылаться на эту строку; без flush строка только в identity map.
    await session.flush()
    return dialog


async def save(
    session: AsyncSession,
    dialog: BotDialog,
    *,
    state: str | None = None,
    data: dict | None = None,
) -> None:
    """Обновить state и/или data. None = поле не трогать."""
    if state is not None:
        dialog.state = state
    if data is not None:
        dialog.data = data
    # updated_at меняет onupdate модели при dirty-атрибутах; flush не обязателен.


async def reset(session: AsyncSession, dialog: BotDialog) -> None:
    """Сбросить FSM в начало: idle и пустой черновик."""
    await save(session, dialog, state="idle", data={})


async def seen(session: AsyncSession, event_id: str) -> bool:
    """Запомнить event_id. True — уже видели (повтор VK), False — впервые.

    INSERT … ON CONFLICT DO NOTHING атомарно: два параллельных воркера
    не обработают одно событие оба. Образец — users.get_or_create.
    """
    stmt = (
        pg_insert(BotEventSeen)
        .values(event_id=event_id)
        .on_conflict_do_nothing(index_elements=["event_id"])
        .returning(BotEventSeen.event_id)
    )
    inserted = (await session.scalars(stmt)).first()
    # Пустой RETURNING = конфликт = строка уже была.
    return inserted is None
