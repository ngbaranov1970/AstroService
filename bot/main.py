"""Точка входа: собирает бота, БД, Redis, LLM и сервисы, запускает polling."""

import asyncio
import logging

import aiosqlite
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from redis.asyncio import Redis

from tg.handlers import router
from config import TIMEZONE, settings
from db import repo
from services.broadcast import broadcast_daily
from services.horoscope import HoroscopeService
from services.llm import LLMClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main() -> None:
    # parse_mode=HTML — тексты в bot/texts.py уже написаны с расчётом на него.
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    db = await aiosqlite.connect(settings.db_path)
    await repo.init_db(db)

    # Redis не критичен: если недоступен, HoroscopeService сам откатится
    # на SQLite (см. services/horoscope.py). Соединение здесь ленивое —
    # реальный коннект произойдёт при первом обращении.
    redis = Redis.from_url(settings.redis_url, decode_responses=True)

    llm = LLMClient()
    horoscope_service = HoroscopeService(db, redis, llm)

    # DI aiogram: любой хендлер получит эти объекты, просто объявив
    # параметр с таким же именем — без глобальных переменных и импортов.
    dp["db"] = db
    dp["redis"] = redis
    dp["horoscope_service"] = horoscope_service

    dp.include_router(router)

    # Ежедневная рассылка гороскопа на день подписчикам — в 08:00 по Москве.
    scheduler = AsyncIOScheduler(timezone=TIMEZONE)
    scheduler.add_job(
        broadcast_daily,
        CronTrigger(hour=8, minute=0, timezone=TIMEZONE),
        args=[bot, db, horoscope_service],
        id="daily_broadcast",
    )
    scheduler.start()

    # Список команд для кнопки-меню (☰) у поля ввода в Telegram.
    await bot.set_my_commands([
        BotCommand(command="start", description="Начать заново"),
        BotCommand(command="sign", description="Сменить знак зодиака"),
        BotCommand(command="sub", description="Подписаться / отписаться от рассылки"),
    ])

    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown()
        await bot.session.close()
        await redis.aclose()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
