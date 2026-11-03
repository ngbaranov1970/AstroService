"""posts kind monthly_horoscope

Revision ID: c8f1a2b3d4e5
Revises: b7e2c4a91f03
Create Date: 2026-11-03

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c8f1a2b3d4e5"
down_revision: Union[str, Sequence[str], None] = "b7e2c4a91f03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Значения PostKind после правки — текстом, а не импортом: миграция описывает
# базу на момент своего написания, а app/models/post.py будет меняться дальше.
KINDS = (
    "daily_sky",
    "sign_lines",
    "daily_horoscope",
    "monthly_horoscope",
    "astro_event",
    "sign_portrait",
    "celebrity",
    "invite",
)
OLD_KINDS = tuple(k for k in KINDS if k != "monthly_horoscope")

# Без префикса ck_posts_ — его дописывает naming_convention из app/db.py
# (подробно — в e4c1a97b2d68_posts_kind_daily_sky.py).
CONSTRAINT = "post_kind"


def _values(kinds: Sequence[str]) -> str:
    return ", ".join(f"'{k}'" for k in kinds)


def upgrade() -> None:
    """Upgrade schema."""
    # kind — VARCHAR со списком в CHECK, новый формат добавляется пересозданием
    # ограничения; autogenerate этого не видит.
    op.drop_constraint(CONSTRAINT, "posts", type_="check")
    op.create_check_constraint(CONSTRAINT, "posts", f"kind IN ({_values(KINDS)})")


def downgrade() -> None:
    """Downgrade schema."""
    # Строки monthly_horoscope противоречат старому CHECK и не дадут его создать.
    op.execute("DELETE FROM posts WHERE kind = 'monthly_horoscope'")
    op.drop_constraint(CONSTRAINT, "posts", type_="check")
    op.create_check_constraint(CONSTRAINT, "posts", f"kind IN ({_values(OLD_KINDS)})")
