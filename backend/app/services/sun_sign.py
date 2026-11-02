"""Знак Солнца по дате без карты: запись в users и событие воронки.

Общая точка для POST /vk/sun-sign и бота: один расчёт, один лог. Про VK и
HTTP не знает — туда уже приходит готовая date.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.services import events
from app.services import users as users_service
from astro_core.transits import sun_sign_on


async def apply(session: AsyncSession, user: User, sign: str) -> bool:
    """Записать знак и залогировать SUN_SIGN_ENTERED. True — значение сменилось.

    Подписку на рассылку не включает: согласие только кнопкой (задача 7).
    """
    changed = users_service.set_sun_sign(user, sign)
    await events.log(session, events.SUN_SIGN_ENTERED, user_id=user.id, sign=sign)
    return changed


async def enter_by_date(
    session: AsyncSession, user: User, birth_date: date
) -> str | None:
    """Знак по дате: записать и вернуть, либо None в день смены знака.

    В день смены пишет событие cusp=True и прежний знак не трогает —
    как раньше делал POST /vk/sun-sign для Mini App.
    """
    sign = sun_sign_on(birth_date)
    if sign is None:
        await events.log(session, events.SUN_SIGN_ENTERED, user_id=user.id, cusp=True)
        return None
    await apply(session, user, sign)
    return sign
