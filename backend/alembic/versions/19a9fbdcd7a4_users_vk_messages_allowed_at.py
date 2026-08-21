"""users vk_messages_allowed_at

Revision ID: 19a9fbdcd7a4
Revises: 719fc65f8a84
Create Date: 2026-08-20 20:17:31.805249

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '19a9fbdcd7a4'
down_revision: Union[str, Sequence[str], None] = '719fc65f8a84'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Nullable и без server_default: колонка добавляется мгновенно, таблица
    # не переписывается. Значение проставляет приложение в момент, когда
    # человек нажал «разрешить» в Mini App.
    op.add_column(
        "users",
        sa.Column("vk_messages_allowed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Откат теряет список разрешивших писать, а восстановить его неоткуда:
    # VK такой список через API не отдаёт.
    op.drop_column("users", "vk_messages_allowed_at")
