"""pairs: таблица пары и парные отчёты

Revision ID: d7a5c31fb904
Revises: e4c1a97b2d68
Create Date: 2026-09-10

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd7a5c31fb904'
down_revision: Union[str, Sequence[str], None] = 'e4c1a97b2d68'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Значения ReportType после правки. Продублированы текстом сознательно:
# миграция описывает базу на момент своего написания, а app/models/report.py
# будет меняться дальше.
TYPES = ('free_natal', 'full_natal', 'pair_free', 'pair_full', 'matrix', 'eastern')
OLD_TYPES = tuple(t for t in TYPES if not t.startswith('pair_'))

# Видов связи два: дружба и работа сняты 12.09.2026 до первого выката
# (см. PairKind в app/models/pair.py). Правится сама миграция, а не вторая
# поверх: в проде этой таблицы ещё нет.
KINDS = ('romance', 'family')

# Имена без префикса: naming_convention из app/db.py дописывает "ck_<таблица>_".
# Передать полное имя — получить ck_reports_ck_reports_report_type и падение.
CK_TYPE = 'report_type'

# CHECK на report_type живёт в ДВУХ таблицах, и колонки в них названы
# по-разному. Забытая orders молчит до первой продажи пары, то есть до
# момента с деньгами в руках.
TYPE_TABLES = (('reports', 'type'), ('orders', 'report_type'))


def _values(items: Sequence[str]) -> str:
    return ', '.join(f"'{i}'" for i in items)


def _set_type_check(allowed: Sequence[str]) -> None:
    for table, column in TYPE_TABLES:
        op.drop_constraint(CK_TYPE, table, type_='check')
        op.create_check_constraint(CK_TYPE, table, f"{column} IN ({_values(allowed)})")


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'pairs',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('token', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('chart_a_id', sa.BigInteger(), nullable=False),
        sa.Column('chart_b_id', sa.BigInteger(), nullable=True),
        sa.Column(
            'kind',
            sa.Enum(*KINDS, name='pair_kind', native_enum=False, create_constraint=True, length=8),
            nullable=False,
        ),
        sa.Column('inviter_label', sa.String(length=64), nullable=True),
        sa.Column('calc', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('calc_version', sa.String(length=32), nullable=True),
        sa.Column('invite_token', sa.String(length=64), nullable=True),
        sa.Column('invite_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('invite_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['chart_a_id'], ['charts.id'], name=op.f('fk_pairs_chart_a_id_charts'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['chart_b_id'], ['charts.id'], name=op.f('fk_pairs_chart_b_id_charts'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_pairs_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_pairs')),
        sa.UniqueConstraint('invite_token', name=op.f('uq_pairs_invite_token')),
        sa.UniqueConstraint('token', name=op.f('uq_pairs_token')),
    )
    op.create_index(op.f('ix_pairs_user_id'), 'pairs', ['user_id'], unique=False)
    op.create_index(
        'uq_pairs_complete',
        'pairs',
        ['user_id', 'chart_a_id', 'chart_b_id', 'kind'],
        unique=True,
        postgresql_where=sa.text('chart_b_id IS NOT NULL'),
    )

    # Тип разбора — VARCHAR со списком значений в CHECK (native_enum=False),
    # поэтому новый продукт добавляется пересозданием ограничения.
    # Autogenerate этого не видит: для него колонка как была VARCHAR, так и
    # осталась, — отсюда рукописная миграция.
    _set_type_check(TYPES)

    # --- reports ---------------------------------------------------------
    op.add_column('reports', sa.Column('pair_id', sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        op.f('fk_reports_pair_id_pairs'), 'reports', 'pairs', ['pair_id'], ['id'], ondelete='CASCADE'
    )
    # Адрес кеша раздваивается: у обычного разбора это карта, у парного — пара.
    # Одним ограничением это не выражается, потому что в Postgres два NULL в
    # UNIQUE не равны и общий ключ снял бы защиту от дублей с натальных
    # отчётов — а на ней держится правило «за один текст платим один раз».
    op.execute('ALTER TABLE reports DROP CONSTRAINT uq_reports_chart_id_type_prompt_version')
    op.create_index(
        'uq_reports_chart_type_version',
        'reports',
        ['chart_id', 'type', 'prompt_version'],
        unique=True,
        postgresql_where=sa.text('pair_id IS NULL'),
    )
    op.create_index(
        'uq_reports_pair_type_version',
        'reports',
        ['pair_id', 'type', 'prompt_version'],
        unique=True,
        postgresql_where=sa.text('pair_id IS NOT NULL'),
    )

    # --- orders ----------------------------------------------------------
    op.add_column('orders', sa.Column('pair_id', sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        op.f('fk_orders_pair_id_pairs'), 'orders', 'pairs', ['pair_id'], ['id'], ondelete='SET NULL'
    )
    # У парного заказа chart_id заполнен картой-якорем, поэтому прежний индекс
    # запретил бы одновременно ждать оплаты разборов «я + Маша» и «я + Лена».
    # Условие индекса autogenerate не сравнивает — пересоздаём руками.
    op.drop_index('uq_orders_one_pending', table_name='orders')
    op.create_index(
        'uq_orders_one_pending',
        'orders',
        ['user_id', 'chart_id', 'report_type'],
        unique=True,
        postgresql_where=sa.text("status = 'pending' AND pair_id IS NULL"),
    )
    op.create_index(
        'uq_orders_one_pending_pair',
        'orders',
        ['user_id', 'pair_id', 'report_type'],
        unique=True,
        postgresql_where=sa.text("status = 'pending' AND pair_id IS NOT NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Откат возможен, только пока пару никто не купил: старый CHECK не примет
    # ни одной строки с типом pair_*, а удалять оплаченный заказ нельзя —
    # это денежный факт. Поэтому громкая ошибка вместо тихой потери денег.
    paid = op.get_bind().execute(
        sa.text(
            "SELECT count(*) FROM orders "
            "WHERE report_type IN ('pair_free', 'pair_full') AND status <> 'pending'"
        )
    ).scalar_one()
    if paid:
        raise RuntimeError(
            f'Откат невозможен: {paid} заказ(ов) на парный разбор уже не в статусе pending. '
            'Историю платежей стирать нельзя — разбирайтесь с ними руками.'
        )

    # Незавершённые заказы денег не содержат, а продукта после отката не
    # существует — снимаем.
    op.execute("DELETE FROM orders WHERE report_type IN ('pair_free', 'pair_full')")
    # Парные тексты тоже уходят: без pair_id их адрес в кеше становится
    # неотличим от натального разбора той же карты-якоря.
    op.execute('DELETE FROM reports WHERE pair_id IS NOT NULL')

    op.drop_index('uq_orders_one_pending_pair', table_name='orders')
    op.drop_index('uq_orders_one_pending', table_name='orders')
    op.create_index(
        'uq_orders_one_pending',
        'orders',
        ['user_id', 'chart_id', 'report_type'],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.drop_constraint(op.f('fk_orders_pair_id_pairs'), 'orders', type_='foreignkey')
    op.drop_column('orders', 'pair_id')

    op.drop_index('uq_reports_pair_type_version', table_name='reports')
    op.drop_index('uq_reports_chart_type_version', table_name='reports')
    op.create_unique_constraint(
        op.f('uq_reports_chart_id_type_prompt_version'), 'reports', ['chart_id', 'type', 'prompt_version']
    )
    op.drop_constraint(op.f('fk_reports_pair_id_pairs'), 'reports', type_='foreignkey')
    op.drop_column('reports', 'pair_id')

    _set_type_check(OLD_TYPES)

    op.drop_index('uq_pairs_complete', table_name='pairs')
    op.drop_index(op.f('ix_pairs_user_id'), table_name='pairs')
    op.drop_table('pairs')
