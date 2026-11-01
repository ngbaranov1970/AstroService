"""Состояние VK-бота: шаг диалога, идемпотентность событий, антиспам комментариев.

Три таблицы под чат-бота в сообщениях сообщества. Общие соглашения пакета —
в app/models/__init__.py. План — docs/vk-bot-plan.md.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    PrimaryKeyConstraint,
    String,
    func,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class BotDialog(Base):
    """Шаг FSM и черновик ввода одного пользователя.

    Одна строка на человека: PK = user_id. В data — промежуточные поля
    (дата, город, кандидаты геокодера), не персональный архив: при удалении
    пользователя строка уходит CASCADE вместе с ним.
    """

    __tablename__ = "bot_dialogs"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )

    # Имена состояний — строки в chatbot/states.py; длина с запасом под новые шаги.
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="idle", server_default=sql_text("'idle'")
    )

    # server_default '{}': код всегда получает словарь, data["x"] не падает на None.
    data: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sql_text("'{}'::jsonb")
    )

    # onupdate — только ORM; сырой UPDATE в psql метку не тронет.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return f"<BotDialog user_id={self.user_id} state={self.state!r}>"


class BotEventSeen(Base):
    """Уже обработанный event_id Callback API — защита от повторов VK.

    VK шлёт одно событие повторно, если «ok» не пришёл вовремя. Повторная
    вставка того же event_id ничего не делает; вызывающий смотрит результат
    seen() и не гоняет диалог второй раз.
    """

    __tablename__ = "bot_events_seen"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<BotEventSeen {self.event_id!r}>"


class BotCommentReply(Base):
    """Уже ответили этому человеку под этим постом стены — антиспам.

    post_id — id поста VK, не наша таблица posts. Связи с контентом нет:
    чужой идентификатор платформы, в нашу схему его тащить незачем.
    """

    __tablename__ = "bot_comment_replies"

    __table_args__ = (PrimaryKeyConstraint("user_id", "post_id"),)

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    post_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<BotCommentReply user_id={self.user_id} post_id={self.post_id}>"
