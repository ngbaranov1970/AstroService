"""bot_dialogs: FSM, идемпотентность Callback, антиспам комментариев

Revision ID: b7e2c4a91f03
Revises: a3d6f1c8e2b4
Create Date: 2026-11-01

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b7e2c4a91f03'
down_revision: Union[str, Sequence[str], None] = 'a3d6f1c8e2b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'bot_dialogs',
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column(
            'state',
            sa.String(length=32),
            server_default=sa.text("'idle'"),
            nullable=False,
        ),
        sa.Column(
            'data',
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['users.id'],
            name=op.f('fk_bot_dialogs_user_id_users'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('user_id', name=op.f('pk_bot_dialogs')),
    )
    op.create_table(
        'bot_events_seen',
        sa.Column('event_id', sa.String(length=64), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('event_id', name=op.f('pk_bot_events_seen')),
    )
    op.create_table(
        'bot_comment_replies',
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('post_id', sa.BigInteger(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['users.id'],
            name=op.f('fk_bot_comment_replies_user_id_users'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint(
            'user_id', 'post_id', name=op.f('pk_bot_comment_replies')
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('bot_comment_replies')
    op.drop_table('bot_events_seen')
    op.drop_table('bot_dialogs')
