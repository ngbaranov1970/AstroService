"""Ручной прогон поста сообщества: дата → небо дня → промпт → DeepSeek → текст.

Служебный аналог кнопки «Сгенерировать неделю» на /admin/content, только без
базы: кеша, очереди и адреса поста здесь нет, каждый запуск платный. Зачем:
- вычитывать новый формат и править промпт по живому тексту, не выкатывая
  прод (правка промпта на сервере — это деплой с --build);
- сравнивать температуры на одном и том же дне;
- мерить расход токенов на день до того, как формат уйдёт в недельный прогон.

Пост о событии (astro_event) сюда не завёрнут: у него другой вход — одно
событие, а выбирает его services/content.py.

Примеры:
    uv run python scripts/content_cli.py
    uv run python scripts/content_cli.py 2026-09-06 --days 3
    uv run python scripts/content_cli.py 2026-09-06 --show-prompt
    uv run python scripts/content_cli.py 2026-09-06 --kind daily_horoscope
    uv run python scripts/content_cli.py 2026-09-28 --kind sign_lines   # на 28-е

Текст поста идёт в stdout (можно перенаправить в файл), служебная информация —
версия промпта, модель, токены — в stderr.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, datetime, timedelta
from inspect import signature
from pathlib import Path

# Скрипт запускают из каталога backend; добавляем его в путь импорта,
# чтобы astro_core и app находились без установки пакета.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.llm import client
from app.llm.prompts import daily_horoscope, daily_sky, sign_lines
from astro_core import transits

# Форматы, у которых вход — сутки целиком. Реестр повторяет _PROMPTS из
# services/content.py, но своим списком: тот тянет за собой модели и базу,
# а скрипт обязан работать там, где Postgres не поднят. У sign_lines дата —
# день гороскопа, а не вечер публикации.
PROMPTS = {
    "daily_sky": daily_sky,
    "sign_lines": sign_lines,
    "daily_horoscope": daily_horoscope,
}


def _user_message(module, day: date) -> str:
    """Данные дня для промпта: небо суток, а для daily_sky ещё и вчерашнее.

    Вчера нужно ради блока отличий (см. build_user в daily_sky). Промпту
    рассылки второй аргумент не нужен, поэтому смотрим на сигнатуру, а не
    перечисляем форматы: список разъехался бы с промптами.
    """
    sky = transits.day_sky(day)
    if "previous" in signature(module.build_user).parameters:
        return module.build_user(sky, transits.day_sky(day - timedelta(days=1)))
    return module.build_user(sky)


def _parse_day(value: str | None) -> date:
    """Дата из аргумента, по умолчанию сегодня по Москве."""
    if value:
        return date.fromisoformat(value)
    # Не date.today(): машина может жить в UTC, и с полуночи до трёх ночи
    # вышли бы вчерашние сутки — та же причина, что в content.today_msk().
    return datetime.now(transits.MSK).date()


def main() -> None:
    parser = argparse.ArgumentParser(description="Пост сообщества на заданный день")
    parser.add_argument("date", nargs="?", help="дата поста, ГГГГ-ММ-ДД (по умолчанию сегодня)")
    parser.add_argument(
        "--days", type=int, default=1, help="сколько дней подряд прогнать (по умолчанию 1)"
    )
    parser.add_argument(
        "--kind",
        choices=sorted(PROMPTS),
        default="daily_sky",
        help="формат поста (по умолчанию daily_sky)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="модель DeepSeek (по умолчанию берётся из промпт-модуля)",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="температура генерации (по умолчанию берётся из промпт-модуля)",
    )
    parser.add_argument(
        "--show-prompt",
        action="store_true",
        help="напечатать system/user промпты и выйти, не тратя токены",
    )
    args = parser.parse_args()

    if args.days < 1:
        sys.exit("--days: нужно хотя бы один день")

    module = PROMPTS[args.kind]
    temperature = args.temperature
    if temperature is None:
        temperature = getattr(module, "TEMPERATURE", client.DEFAULT_TEMPERATURE)
    max_tokens = getattr(module, "MAX_TOKENS", 4096)
    # Модель по умолчанию — та же, что возьмёт прод (services/content.py
    # читает MODEL так же). Иначе отладка шла бы на одной модели, а лента
    # выходила на другой.
    model = args.model or getattr(module, "MODEL", client.DEFAULT_MODEL)

    start = _parse_day(args.date)
    days = [start + timedelta(days=offset) for offset in range(args.days)]

    print(f"Промпт: {module.VERSION}", file=sys.stderr)

    if args.show_prompt:
        print("--- system ---")
        print(module.SYSTEM)
        for day in days:
            print(f"--- user, {day} ---")
            print(_user_message(module, day))
        return

    print(
        f"Модель: {model}, temperature {temperature}, max_tokens {max_tokens}\n",
        file=sys.stderr,
    )

    # Готовый текст промпт собирает сам: у daily_sky из JSON выходят заголовок,
    # абзацы и концовка, читать сырой ответ в терминале невозможно.
    render = getattr(module, "render", None)

    async def _run() -> tuple[int, int]:
        """Прогоняет дни по очереди, печатая каждый сразу. Возвращает сумму токенов."""
        prompt_tokens = completion_tokens = 0
        try:
            for day in days:
                print(f"[{day}] иду в DeepSeek…", file=sys.stderr)
                result = await client.chat(
                    module.SYSTEM,
                    _user_message(module, day),
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )

                # Печатаем внутри цикла, а не в конце: падение на третьем дне
                # иначе унесло бы два уже оплаченных текста.
                print(f"===== {day} =====")
                print(render(result.text) if render else result.text)
                print()

                u = result.usage
                prompt_tokens += u.prompt_tokens
                completion_tokens += u.completion_tokens
                print(
                    f"[{day}] промпт {u.prompt_tokens} ток. "
                    f"(из кеша {u.cached_prompt_tokens}), ответ {u.completion_tokens} ток.",
                    file=sys.stderr,
                )
        finally:
            await client.close_client()
        return prompt_tokens, completion_tokens

    prompt_tokens, completion_tokens = asyncio.run(_run())

    if len(days) > 1:
        print(
            f"\nИтого за {len(days)} дн.: промпт {prompt_tokens} ток., "
            f"ответ {completion_tokens} ток.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
