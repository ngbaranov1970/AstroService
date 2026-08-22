"""posts

Revision ID: c7a41d6b90f2
Revises: 19a9fbdcd7a4
Create Date: 2026-08-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7a41d6b90f2'
down_revision: Union[str, Sequence[str], None] = '19a9fbdcd7a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Новая таблица, к существующим не привязана: пост ничей — ни пользователя,
    # ни карты, ни внешних ключей. Значит миграция не блокирует ничего живого.
    op.create_table(
        'posts',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column(
            'kind',
            sa.Enum(
                'daily_horoscope', 'astro_event', 'sign_portrait', 'celebrity', 'invite',
                name='post_kind', native_enum=False, create_constraint=True, length=24,
            ),
            nullable=False,
        ),
        # Date, а не timestamptz: пост адресован суткам по Москве (см. Post
        # в app/models/post.py).
        sa.Column('target_date', sa.Date(), nullable=False),
        sa.Column('prompt_version', sa.String(length=32), nullable=False),
        # Пустая строка вместо NULL: в уникальном индексе Postgres два NULL
        # считаются разными, и защита от повторной генерации не сработала бы
        # для форматов без события (см. Post.event_key в app/models/post.py).
        sa.Column('event_key', sa.String(length=64), server_default=sa.text("''"), nullable=False),
        sa.Column('title', sa.Text(), nullable=True),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('model', sa.String(length=64), nullable=False),
        sa.Column('temperature', sa.Float(), nullable=False),
        sa.Column('tokens_prompt', sa.Integer(), nullable=False),
        sa.Column('tokens_completion', sa.Integer(), nullable=False),
        sa.Column('tokens_cached', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_posts')),
        # Ключ кеша: повторный запуск генератора за уже написанное событие
        # не платит DeepSeek второй раз. Событие входит в ключ вместе с датой —
        # иначе в сутки помещался бы ровно один пост, а 30.06.2026 это
        # полнолуние И вход Юпитера в знак.
        sa.UniqueConstraint(
            'kind', 'target_date', 'event_key', 'prompt_version',
            name=op.f('uq_posts_kind_target_date_event_key_prompt_version'),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Откат теряет очередь неопубликованных текстов и расход токенов за период.
    # Тексты восстановимы только повторной генерацией, то есть за деньги.
    op.drop_table('posts')
