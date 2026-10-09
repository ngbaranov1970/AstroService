"""orders: пол клиента VIP-разбора

Revision ID: c5a9e7d3b2f1
Revises: f2b8d4c6a1e3
Create Date: 2026-10-09

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c5a9e7d3b2f1'
down_revision: Union[str, Sequence[str], None] = 'f2b8d4c6a1e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Имя без префикса: "ck_<таблица>_" дописывает naming_convention из app/db.py.
CK_GENDER = 'client_gender'


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('orders', sa.Column('client_gender', sa.String(length=8), nullable=True))
    op.create_check_constraint(CK_GENDER, 'orders', "client_gender IN ('female', 'male')")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(CK_GENDER, 'orders', type_='check')
    op.drop_column('orders', 'client_gender')
