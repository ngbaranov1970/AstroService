"""users sun_sign

Revision ID: b3e8f2a6d914
Revises: a9d3f5e1c2b7
Create Date: 2026-09-29

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b3e8f2a6d914'
down_revision: Union[str, Sequence[str], None] = 'a9d3f5e1c2b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Знаки текстом, а не импортом SIGNS_RU: миграция описывает базу на момент
# своего написания.
SIGNS = (
    'Овен', 'Телец', 'Близнецы', 'Рак', 'Лев', 'Дева',
    'Весы', 'Скорпион', 'Стрелец', 'Козерог', 'Водолей', 'Рыбы',
)

# Без префикса ck_users_ — его дописывает naming_convention из app/db.py.
CONSTRAINT = 'sun_sign'


def upgrade() -> None:
    """Upgrade schema."""
    # Nullable без server_default: существующие строки не переписываются.
    op.add_column('users', sa.Column('sun_sign', sa.String(length=16), nullable=True))
    values = ', '.join(f"'{s}'" for s in SIGNS)
    op.create_check_constraint(CONSTRAINT, 'users', f"sun_sign IN ({values})")


def downgrade() -> None:
    """Downgrade schema."""
    # Введённые знаки теряются; вернуть их можно только повторным вводом даты.
    op.drop_constraint(CONSTRAINT, 'users', type_='check')
    op.drop_column('users', 'sun_sign')
