"""posts kind daily_sky

Revision ID: e4c1a97b2d68
Revises: b125c0bdeff7
Create Date: 2026-09-05

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e4c1a97b2d68'
down_revision: Union[str, Sequence[str], None] = 'b125c0bdeff7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Значения PostKind после правки. Список продублирован здесь текстом
# сознательно: миграция описывает базу на момент своего написания, а
# app/models/post.py будет меняться дальше.
KINDS = (
    'daily_sky', 'daily_horoscope', 'astro_event',
    'sign_portrait', 'celebrity', 'invite',
)
OLD_KINDS = tuple(k for k in KINDS if k != 'daily_sky')


def _values(kinds: Sequence[str]) -> str:
    return ', '.join(f"'{k}'" for k in kinds)


def upgrade() -> None:
    """Upgrade schema."""
    # kind — это VARCHAR(24) со списком значений в CHECK (native_enum=False,
    # см. Post.kind), поэтому новый формат поста добавляется не типом, а
    # пересозданием ограничения. Autogenerate этого не видит: для него колонка
    # как была VARCHAR, так и осталась.
    op.drop_constraint('ck_posts_post_kind', 'posts', type_='check')
    op.create_check_constraint('ck_posts_post_kind', 'posts', f"kind IN ({_values(KINDS)})")


def downgrade() -> None:
    """Downgrade schema."""
    # Строки daily_sky, если они уже сгенерированы, старому CHECK противоречат
    # и создать его не дадут — удаляем их. Возврат назад означает отказ от
    # формата, тексты в нём смысла не имеют.
    op.execute("DELETE FROM posts WHERE kind = 'daily_sky'")
    op.drop_constraint('ck_posts_post_kind', 'posts', type_='check')
    op.create_check_constraint('ck_posts_post_kind', 'posts', f"kind IN ({_values(OLD_KINDS)})")
