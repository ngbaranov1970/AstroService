"""orders: VIP-разбор — тип vip_natal, черновик и отметка доставки

Revision ID: f2b8d4c6a1e3
Revises: b3e8f2a6d914
Create Date: 2026-10-08

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'f2b8d4c6a1e3'
down_revision: Union[str, Sequence[str], None] = 'b3e8f2a6d914'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Значения ReportType текстом, а не из app/models: миграция описывает базу
# на момент написания.
OLD_TYPES = ('free_natal', 'full_natal', 'pair_free', 'pair_full', 'matrix', 'eastern')
TYPES = OLD_TYPES + ('vip_natal',)

# Имя без префикса: "ck_<таблица>_" дописывает naming_convention из app/db.py.
CK_TYPE = 'report_type'

# CHECK на тип живёт в двух таблицах с разными именами колонки — как в d7a5c31fb904.
TYPE_TABLES = (('reports', 'type'), ('orders', 'report_type'))


def _values(items: Sequence[str]) -> str:
    return ', '.join(f"'{i}'" for i in items)


def _set_type_check(allowed: Sequence[str]) -> None:
    for table, column in TYPE_TABLES:
        op.drop_constraint(CK_TYPE, table, type_='check')
        op.create_check_constraint(CK_TYPE, table, f"{column} IN ({_values(allowed)})")


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('orders', sa.Column('draft_text', sa.Text(), nullable=True))
    op.add_column('orders', sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True))
    _set_type_check(TYPES)


def downgrade() -> None:
    """Downgrade schema."""
    # Оплаченный VIP-заказ — денежный факт: старый CHECK его не примет, а удалять нельзя.
    paid = op.get_bind().execute(
        sa.text("SELECT count(*) FROM orders WHERE report_type = 'vip_natal' AND status <> 'pending'")
    ).scalar_one()
    if paid:
        raise RuntimeError(
            f'Откат невозможен: {paid} VIP-заказ(ов) уже не в статусе pending. '
            'Историю платежей стирать нельзя — разбирайтесь с ними руками.'
        )

    op.execute("DELETE FROM orders WHERE report_type = 'vip_natal'")
    op.execute("DELETE FROM reports WHERE type = 'vip_natal'")
    _set_type_check(OLD_TYPES)
    op.drop_column('orders', 'delivered_at')
    op.drop_column('orders', 'draft_text')
