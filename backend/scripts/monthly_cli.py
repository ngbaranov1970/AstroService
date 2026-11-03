"""Генерация гороскопа на месяц в таблицу posts (кеш для кнопки бота).

Место в слоях: cron 1-го числа / ручной запуск → ЭТОТ ФАЙЛ →
app/services/content.generate_month → DeepSeek → posts.

Без базы content_cli.py (kind monthly_horoscope) только печатает текст;
этот скрипт пишет строку, которую потом читает chatbot.flows.menu.reply_month.

Примеры:
    uv run python scripts/monthly_cli.py
    uv run python scripts/monthly_cli.py --date 2026-11-01
    uv run python scripts/monthly_cli.py --date 2026-11-15   # нормализуется к 1-му

Коды выхода: 0 — пост есть (новый или уже был), 1 — LLM не ответил.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import dispose_engine, get_sessionmaker
from app.llm import client
from app.llm.prompts import monthly_horoscope
from app.logging_setup import setup_logging
from app.services import content


async def _run(month_start: date | None) -> int:
    sessionmaker = get_sessionmaker()
    try:
        async with sessionmaker() as session:
            post = await content.generate_month(session, month_start)
    finally:
        await client.close_client()
        await dispose_engine()

    if post is None:
        print("месячный гороскоп не сгенерировался", file=sys.stderr)
        return 1

    target = post.target_date
    print(
        f"{target}: {post.prompt_version}, "
        f"пост id={post.id}, ответ {post.tokens_completion} ток."
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Гороскоп на месяц → posts")
    parser.add_argument(
        "--date",
        help="любая дата месяца, ГГГГ-ММ-ДД (по умолчанию текущий месяц по Москве)",
    )
    args = parser.parse_args()

    month_start: date | None = None
    if args.date:
        try:
            month_start = monthly_horoscope.month_start_of(date.fromisoformat(args.date))
        except ValueError:
            sys.exit(f"--date {args.date!r}: ожидается ГГГГ-ММ-ДД")

    setup_logging()
    sys.exit(asyncio.run(_run(month_start)))


if __name__ == "__main__":
    main()
