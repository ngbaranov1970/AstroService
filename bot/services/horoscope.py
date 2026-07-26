"""Выдача гороскопов: кеш Redis → SQLite → генерация DeepSeek."""

import asyncio
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta

import aiosqlite
from redis.asyncio import Redis

from tg.texts import SIGNS
from config import TIMEZONE
from db import repo
from services.llm import LLMClient, truncate_horoscope

logger = logging.getLogger(__name__)


def period_key(period: str, today: date | None = None) -> str:
    """«Адрес» гороскопа во времени: 2026-07-12 / 2026-W28 / 2026-07 / 2026."""
    today = today or datetime.now(TIMEZONE).date()
    if period == "day":
        return today.isoformat()
    if period == "week":
        year, week, _ = today.isocalendar()
        return f"{year}-W{week:02d}"
    if period == "month":
        return f"{today:%Y-%m}"
    if period == "year":
        return str(today.year)
    raise ValueError(f"Неизвестный период: {period}")


def seconds_until_period_end(period: str, now: datetime | None = None) -> int:
    """TTL для Redis — до конца периода, но не больше 30 дней."""
    now = now or datetime.now(TIMEZONE)
    today = now.date()
    if period == "day":
        end = today + timedelta(days=1)
    elif period == "week":
        end = today + timedelta(days=8 - today.isoweekday())
    elif period == "month":
        end = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
    else:  # year
        end = date(today.year + 1, 1, 1)
    # tzinfo берём из now, а не из константы: тогда функция одинаково
    # работает и с aware-временем из бота, и с naive-временем в тестах.
    seconds = int(
        (datetime.combine(end, datetime.min.time(), tzinfo=now.tzinfo) - now).total_seconds()
    )
    return max(60, min(seconds, 30 * 24 * 3600))


class HoroscopeService:
    """Гороскопы: кеш Redis → SQLite → генерация DeepSeek. Объединяет три уровня хранения, чтобы не генерировать гороскопы дважды и не терять их при рестарте бота."""
    def __init__(
        self, db: aiosqlite.Connection, redis: Redis | None, llm: LLMClient
    ) -> None:
        self._db = db
        self._redis = redis
        self._llm = llm
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def _redis_get(self, key: str) -> str | None:
        if self._redis is None:
            return None
        try:
            return await self._redis.get(key)
        except Exception as exc:
            logger.warning("Redis (get) недоступен: %s", exc)
            return None
        
    async def _redis_set(self, key: str, value: str, ttl: int) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.set(key, value, ex=ttl)
        except Exception as exc:
            logger.warning("Redis (set) недоступен: %s", exc)

    async def get(self, sign: str, period: str) -> str:
        key = period_key(period)
        cache_key = f"horo:{sign}:{period}:{key}"

        # 1. Redis — самый быстрый уровень
        cached = await self._redis_get(cache_key)
        if cached:
            return truncate_horoscope(cached)

        # Блокировка на ключ: параллельные запросы одного знака
        # не должны запускать генерацию дважды
        async with self._locks[cache_key]:
            # 2. SQLite — переживает рестарты Redis и бота
            text = await repo.get_horoscope(self._db, sign, period, key)
            if text is None:
                # 3. Генерация — медленно и платно, поэтому в самом конце
                sign_name = SIGNS[sign][0]
                logger.info("Генерирую: %s / %s / %s", sign, period, key)
                text = await self._llm.generate_horoscope(sign_name, period)
                await repo.save_horoscope(self._db, sign, period, key, text)
            else:
                # Записи, сохранённые до появления лимита длины в LLMClient,
                # могли пережить его и остаться длиннее лимита Telegram.
                text = truncate_horoscope(text)

        await self._redis_set(cache_key, text, seconds_until_period_end(period))
        return text




