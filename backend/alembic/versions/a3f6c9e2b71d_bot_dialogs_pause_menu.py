"""bot_dialogs: пауза бота по «стоп» владельца и время последнего приветствия

Revision ID: a3f6c9e2b71d
Revises: d2e7b9a4c6f1
Create Date: 2026-11-18

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a3f6c9e2b71d'
down_revision: Union[str, Sequence[str], None] = 'd2e7b9a4c6f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('bot_dialogs', sa.Column('paused_until', sa.DateTime(timezone=True), nullable=True))
    op.add_column('bot_dialogs', sa.Column('menu_shown_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('bot_dialogs', 'menu_shown_at')
    op.drop_column('bot_dialogs', 'paused_until')
