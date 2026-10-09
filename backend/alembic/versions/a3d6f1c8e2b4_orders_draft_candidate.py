"""orders: вариант VIP-черновика на принятие

Revision ID: a3d6f1c8e2b4
Revises: c5a9e7d3b2f1
Create Date: 2026-10-09

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a3d6f1c8e2b4'
down_revision: Union[str, Sequence[str], None] = 'c5a9e7d3b2f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('orders', sa.Column('draft_candidate', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('orders', 'draft_candidate')
