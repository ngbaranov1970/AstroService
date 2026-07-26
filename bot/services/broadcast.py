"""Ежедневная рассылка гороскопа на день подписчикам.

Вызывается из APScheduler-джобы в main.py — сам модуль ничего не знает
о расписании, только о том, как разослать один раз.
"""

import asyncio
import logging
from collections import defaultdict

import aiosqlite
from aiogram import Bot

from tg.keyboards import horoscope_keyboard
from tg.texts import HOROSCOPE, PERIODS, SIGNS
from db import repo
from services.horoscope import HoroscopeService

logger = logging.getLogger(__name__)

PERIOD = "day"


async def broadcast_daily(
    bot: Bot, db: aiosqlite.Connection, horoscope_service: HoroscopeService
) -> None:
    """Разослать гороскоп на день всем подписчикам."""
    subscribers = await repo.get_subscribers(db)

    # Группируем по знаку, чтобы сгенерировать/достать из кеша гороскоп
    # один раз на знак, а не по разу на каждого подписчика этого знака.
    by_sign: defaultdict[str, list[int]] = defaultdict(list)
    for user_id, sign in subscribers:
        by_sign[sign].append(user_id)

    for sign, user_ids in by_sign.items():
        try:
            text = await horoscope_service.get(sign, PERIOD)
        except Exception:
            logger.exception("Рассылка: не удалось сгенерировать гороскоп для %s", sign)
            continue

        if not text or not text.strip():
            logger.error("Рассылка: получен пустой гороскоп для %s, отправка пропущена", sign)
            continue

        sign_name, emoji, _ = SIGNS[sign]
        message = HOROSCOPE.format(
            emoji=emoji, sign=sign_name, period_label=PERIODS[PERIOD], text=text
        )
        keyboard = horoscope_keyboard(current_period=PERIOD, subscribed=True)

        for user_id in user_ids:
            try:
                await bot.send_message(user_id, message, reply_markup=keyboard)
            except Exception:
                # Например, пользователь заблокировал бота — это не повод
                # прерывать рассылку остальным подписчикам.
                logger.warning(
                    "Рассылка: не удалось отправить пользователю %s", user_id, exc_info=True
                )
            # Не быстрее ~20 сообщений в секунду: у Telegram лимит ~30/с,
            # при его превышении отправка падает с RetryAfter.
            await asyncio.sleep(0.05)
