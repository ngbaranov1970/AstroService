"""Ручной тест LLM-клиента: задай знак и период ниже и запусти файл."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.llm import LLMClient

SIGN = "Лев"  # знак зодиака: овен, телец, близнецы, рак, лев, дева, весы, скорпион, стрелец, козерог, водолей, рыбы
PERIOD = "day"  # day | week | month | year


async def main() -> None:
    client = LLMClient()
    text = await client.generate_horoscope(SIGN, PERIOD)
    print(text)


asyncio.run(main())
