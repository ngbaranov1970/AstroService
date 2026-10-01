"""Разово заполнить users.platform_username у VK-строк, заведённых до 01.10.2026.

Место в слоях: терминал → ЭТОТ ФАЙЛ → app/vk/api.py (users.get) и
app/services/users.py. Новые строки заполняет сам вход (users.fill_vk_name),
скрипт закрывает только старый хвост. Трогает лишь пустые имена, поэтому
перезапуск безвреден. Решение — docs/astro-context.md, «Карточка человека».

Пример:
    uv run python scripts/fill_vk_names.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Скрипт запускают из каталога backend; добавляем его в путь импорта,
# чтобы app находился без установки пакета.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.db import dispose_engine, get_sessionmaker
from app.logging_setup import setup_logging
from app.models import Platform, User
from app.services import users as users_service
from app.vk import api as vk_api


async def _run() -> int:
    """Заполнить имена и вернуть код выхода процесса."""
    sessionmaker = get_sessionmaker()
    try:
        async with sessionmaker() as session:
            stmt = select(User).where(
                User.platform == Platform.VK,
                User.platform_username.is_(None),
                User.deleted_at.is_(None),
            )
            pending = list(await session.scalars(stmt))
            names = await vk_api.get_full_names([int(u.external_id) for u in pending])

            filled = 0
            for user in pending:
                name = names.get(int(user.external_id))
                if name and users_service.set_platform_username(user, name):
                    filled += 1
            await session.commit()
    finally:
        await vk_api.close_client()
        await dispose_engine()

    # Без имён в выводе: stdout оседает в логах (CLAUDE.md).
    print(f"имя из VK заполнено у {filled} из {len(pending)}")
    # Пустой ответ на непустой список — VK не ответил, а не «все удалены».
    return 1 if pending and not names else 0


def main() -> None:
    setup_logging()
    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
