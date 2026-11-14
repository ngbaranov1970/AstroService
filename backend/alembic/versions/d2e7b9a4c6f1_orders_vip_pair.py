"""orders: разбор пары из VK-бота — тип vip_pair и данные партнёра

Revision ID: d2e7b9a4c6f1
Revises: c8f1a2b3d4e5
Create Date: 2026-11-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd2e7b9a4c6f1'
down_revision: Union[str, Sequence[str], None] = 'c8f1a2b3d4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Значения ReportType текстом, а не из app/models: миграция описывает базу
# на момент написания.
OLD_TYPES = (
    'free_natal', 'full_natal', 'pair_free', 'pair_full', 'vip_natal', 'matrix', 'eastern',
)
TYPES = OLD_TYPES + ('vip_pair',)

# Имя без префикса: "ck_<таблица>_" дописывает naming_convention из app/db.py.
CK_TYPE = 'report_type'

# CHECK на тип живёт в двух таблицах с разными именами колонки — как в f2b8d4c6a1e3.
TYPE_TABLES = (('reports', 'type'), ('orders', 'report_type'))


def _values(items: Sequence[str]) -> str:
    return ', '.join(f"'{i}'" for i in items)


def _set_type_check(allowed: Sequence[str]) -> None:
    for table, column in TYPE_TABLES:
        op.drop_constraint(CK_TYPE, table, type_='check')
        op.create_check_constraint(CK_TYPE, table, f"{column} IN ({_values(allowed)})")


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('orders', sa.Column('pair_data', postgresql.JSONB(), nullable=True))
    _set_type_check(TYPES)


def downgrade() -> None:
    """Downgrade schema."""
    # Оплаченный заказ пары — денежный факт: старый CHECK его не примет, а удалять нельзя.
    paid = op.get_bind().execute(
        sa.text("SELECT count(*) FROM orders WHERE report_type = 'vip_pair' AND status <> 'pending'")
    ).scalar_one()
    if paid:
        raise RuntimeError(
            f'Откат невозможен: {paid} заказ(ов) vip_pair уже не в статусе pending. '
            'Историю платежей стирать нельзя — разбирайтесь с ними руками.'
        )

    op.execute("DELETE FROM orders WHERE report_type = 'vip_pair'")
    op.execute("DELETE FROM reports WHERE type = 'vip_pair'")
    _set_type_check(OLD_TYPES)
    op.drop_column('orders', 'pair_data')
