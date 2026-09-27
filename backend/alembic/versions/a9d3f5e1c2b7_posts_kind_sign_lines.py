"""posts kind sign_lines

Revision ID: a9d3f5e1c2b7
Revises: d7a5c31fb904
Create Date: 2026-09-27

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a9d3f5e1c2b7'
down_revision: Union[str, Sequence[str], None] = 'd7a5c31fb904'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Значения PostKind после правки — текстом, а не импортом: миграция описывает
# базу на момент своего написания, а app/models/post.py будет меняться дальше.
KINDS = (
    'daily_sky', 'sign_lines', 'daily_horoscope', 'astro_event',
    'sign_portrait', 'celebrity', 'invite',
)
OLD_KINDS = tuple(k for k in KINDS if k != 'sign_lines')

# Без префикса ck_posts_ — его дописывает naming_convention из app/db.py
# (подробно — в e4c1a97b2d68_posts_kind_daily_sky.py).
CONSTRAINT = 'post_kind'


def _values(kinds: Sequence[str]) -> str:
    return ', '.join(f"'{k}'" for k in kinds)


def upgrade() -> None:
    """Upgrade schema."""
    # kind — VARCHAR со списком в CHECK, новый формат добавляется пересозданием
    # ограничения; autogenerate этого не видит.
    op.drop_constraint(CONSTRAINT, 'posts', type_='check')
    op.create_check_constraint(CONSTRAINT, 'posts', f"kind IN ({_values(KINDS)})")


def downgrade() -> None:
    """Downgrade schema."""
    # Строки sign_lines противоречат старому CHECK и не дадут его создать.
    op.execute("DELETE FROM posts WHERE kind = 'sign_lines'")
    op.drop_constraint(CONSTRAINT, 'posts', type_='check')
    op.create_check_constraint(CONSTRAINT, 'posts', f"kind IN ({_values(OLD_KINDS)})")
