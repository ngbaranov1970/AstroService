"""Сквозной прогон отчёта: дата + город → geo → natal → промпт → DeepSeek → текст.

Это ручной аналог будущего пути «POST /api/charts → POST /api/reports/free»,
только в терминале и без базы. Зачем:
- увидеть первый живой текст разбора и править промпт по нему;
- калибровать temperature (см. DEFAULT_TEMPERATURE в app/llm/client.py):
  гоняем одну и ту же карту с разными значениями и сравниваем глазами;
- мерить расход токенов до того, как подключим лимиты и кеш.

Примеры:
    uv run python scripts/report_cli.py 1990-05-11 17:20 Касимов --name Николай
    uv run python scripts/report_cli.py 1990-05-11 17:20 Касимов --temperature 1.0
    uv run python scripts/report_cli.py 1990-05-11 17:20 Касимов --show-prompt
    uv run python scripts/report_cli.py 1990-05-11 17:20 Касимов --prompt full_natal
    uv run python scripts/report_cli.py 1990-05-11 - Касимов --prompt full_natal

Текст отчёта идёт в stdout (можно перенаправить в файл), вся служебная
информация — место, модель, токены — в stderr.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

# Скрипт запускают из каталога backend; добавляем его в путь импорта,
# чтобы astro_core и app находились без установки пакета.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from astro_core.geo import City, search_cities, tz_at  # noqa: E402
from astro_core.natal import compute_natal  # noqa: E402
from app.llm import client  # noqa: E402
from app.llm.prompts import free_natal, full_natal  # noqa: E402
from app.services.charts import UNKNOWN_TIME  # noqa: E402

# Промпты подключаются по имени флага --prompt: каждый модуль сам знает
# свой VERSION/SYSTEM/build_user, скрипт про их устройство не знает ничего
# сверх этого общего контракта (см. docstring free_natal.py). Необязательные
# TEMPERATURE/MAX_TOKENS/render() читаем через getattr — промпт, который
# отвечает готовым текстом, обходится без них.
PROMPTS = {"free_natal": free_natal, "full_natal": full_natal}

# Время «-» — неизвестно, как в pair_report_cli.py.
UNKNOWN_MARK = "-"


def _resolve_place(args: argparse.Namespace) -> tuple[float, float, str, str]:
    """Возвращает (lat, lon, tz, подпись места) из --lat/--lon или по имени города.

    Копия логики natal_cli.py: скрипты намеренно самостоятельные, общий
    помощник появится вместе с app/services/charts.py — там этот путь
    станет продуктовым кодом, а не служебным.
    """
    if args.lat is not None and args.lon is not None:
        tz = args.tz or tz_at(args.lat, args.lon)
        return args.lat, args.lon, tz, f"({args.lat}, {args.lon})"

    if not args.city:
        sys.exit("Укажите город или пару --lat/--lon")

    found = search_cities(args.city)
    if not found:
        sys.exit(f"Город не найден: {args.city!r}")

    if len(found) > 1:
        print("Найдены города (выбран отмеченный, другой — через --pick N):", file=sys.stderr)
        for i, c in enumerate(found, 1):
            mark = "→" if i == args.pick else " "
            print(
                f" {mark} {i}. {c.display_name} ({c.country}), нас. {c.population:,}, {c.tz}",
                file=sys.stderr,
            )
        print(file=sys.stderr)

    if not 1 <= args.pick <= len(found):
        sys.exit(f"--pick {args.pick}: вариантов всего {len(found)}")
    city: City = found[args.pick - 1]
    return city.lat, city.lon, city.tz, f"{city.display_name} ({city.country})"


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM-разбор натальной карты")
    parser.add_argument("date", help="дата рождения, ГГГГ-ММ-ДД")
    parser.add_argument("time", help=f"местное время рождения, ЧЧ:ММ или «{UNKNOWN_MARK}»")
    parser.add_argument("city", nargs="?", help="город рождения (поиск по базе GeoNames)")
    parser.add_argument("--pick", type=int, default=1, help="номер города из подсказок")
    parser.add_argument("--lat", type=float, help="широта вместо города")
    parser.add_argument("--lon", type=float, help="долгота вместо города")
    parser.add_argument("--tz", help="зона IANA (иначе определяется по координатам)")
    parser.add_argument("--name", help="имя для персонализации текста")
    parser.add_argument(
        "--prompt",
        choices=sorted(PROMPTS),
        default="free_natal",
        help="какой промпт-модуль использовать (по умолчанию free_natal)",
    )
    parser.add_argument("--model", default=client.DEFAULT_MODEL, help="модель DeepSeek")
    parser.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="температура генерации (по умолчанию берётся из промпт-модуля, "
        "иначе client.DEFAULT_TEMPERATURE)",
    )
    parser.add_argument(
        "--show-prompt",
        action="store_true",
        help="напечатать system/user промпты и выйти, не тратя токены",
    )
    args = parser.parse_args()

    prompt = PROMPTS[args.prompt]
    # full_natal рекомендует свою temperature/max_tokens (см. TEMPERATURE/
    # MAX_TOKENS в модуле) — они откалиброваны под другой формат ответа
    # (JSON-секции вместо связного markdown), чем free_natal_v1.
    temperature = args.temperature
    if temperature is None:
        temperature = getattr(prompt, "TEMPERATURE", client.DEFAULT_TEMPERATURE)
    max_tokens = getattr(prompt, "MAX_TOKENS", 4096)

    # Неизвестное время считаем от того же полудня, что и продукт (services/charts.py).
    time_known = args.time != UNKNOWN_MARK
    birth_time = args.time if time_known else f"{UNKNOWN_TIME:%H:%M}"
    birth_dt = datetime.strptime(f"{args.date} {birth_time}", "%Y-%m-%d %H:%M")
    lat, lon, tz, place = _resolve_place(args)

    natal = compute_natal(birth_dt, lat, lon, tz)
    user_prompt = prompt.build_user(natal, name=args.name, time_known=time_known)

    print(f"Место: {place}   tz {tz}", file=sys.stderr)
    print(f"Промпт: {prompt.VERSION}", file=sys.stderr)

    if args.show_prompt:
        print("--- system ---")
        print(prompt.SYSTEM)
        print("--- user ---")
        print(user_prompt)
        return

    print(
        f"Модель: {args.model}, temperature {temperature}, max_tokens {max_tokens}\n",
        file=sys.stderr,
    )

    async def _run() -> client.LLMResult:
        try:
            return await client.chat(
                prompt.SYSTEM,
                user_prompt,
                model=args.model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        finally:
            await client.close_client()

    result = asyncio.run(_run())

    # full_natal отвечает JSON-секциями (этого требует пейволл) — читать его
    # в терминале невозможно, поэтому модуль сам приводит свой ответ к тексту.
    render = getattr(prompt, "render", None)
    print(render(result.text) if render else result.text)

    u = result.usage
    print(
        f"\n[{result.model}] промпт {u.prompt_tokens} ток. "
        f"(из кеша {u.cached_prompt_tokens}), ответ {u.completion_tokens} ток.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
