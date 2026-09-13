"""Разбор пары в терминале: две даты → синастрия → промпт → DeepSeek → текст.

Инструмент приёмки промптов пары, не продуктовый код. Место в слоях:
ЭТОТ ФАЙЛ → astro_core (geo, natal, synastry) → app/llm/prompts + client.

Отдельно от pair_cli.py, и вот почему. Тот работает через services/pairs.py и
services/reports.py, где текст кешируется по адресу (pair_id, тип, версия
промпта): второй запуск на той же паре вернёт ТОТ ЖЕ текст, не сходив в
DeepSeek. А приёмка промпта устроена наоборот — три прогона одной пары и
сравнение сюжетов. Поэтому здесь нет ни базы, ни кеша: каждый запуск считает
карты заново и платит за новый текст.

Примеры:
    uv run python scripts/pair_report_cli.py --kind romance \\
        --name-a Коля --name-b Маша \\
        1990-05-11 17:20 Касимов 1992-01-10 04:30 Москва

    uv run python scripts/pair_report_cli.py --prompt pair_full --kind family \\
        --runs 3 1990-05-11 17:20 Касимов 1992-01-10 04:30 Москва

    uv run python scripts/pair_report_cli.py --kind work --show-prompt \\
        1988-04-05 00:20 Алматы 2001-07-14 - Владивосток

Текст идёт в stdout, служебные строки — в stderr, копия каждого прогона
ложится файлом в ~/Документы/Astro/pairs (отключается флагом --no-save).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

# Скрипт запускают из каталога backend; добавляем его в путь импорта, чтобы
# app и astro_core находились без установки пакета.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.llm import client  # noqa: E402
from app.llm.prompts import pair_free, pair_full  # noqa: E402

# Полдень при неизвестном времени берём у продукта, а не заводим свой: считать
# приёмкой не то, что потом посчитает сервис, — значит вычитывать чужой текст.
from app.services.charts import UNKNOWN_TIME  # noqa: E402
from astro_core.geo import City, search_cities  # noqa: E402
from astro_core.natal import compute_natal  # noqa: E402
from astro_core.synastry import compute_synastry  # noqa: E402

# Промпты подключаются по имени флага --prompt: каждый модуль знает свой
# VERSION/SYSTEM/build_user, скрипт про их устройство не знает ничего сверх
# общего контракта. Необязательные TEMPERATURE/MAX_TOKENS/render() читаем
# через getattr.
PROMPTS = {"pair_free": pair_free, "pair_full": pair_full}

# Типы связи. Дублируют PairKind из моделей намеренно: скрипт базы не видит
# вовсе, а импорт моделей ради двух строк потянул бы за собой SQLAlchemy.
KINDS = ("romance", "family")

# Сюда ложатся файлы прогонов. Домашние «Документы», а не tmp: приёмку читают
# глазами через час и через неделю, а tmp чистится и теряется.
DEFAULT_OUT = Path.home() / "Документы" / "Astro" / "pairs"

# Метка «время неизвестно» в аргументах, как в pair_cli.py.
UNKNOWN_MARK = "-"


def _resolve_city(query: str, pick: int) -> City:
    """Город по названию, с подсказками в stderr, когда вариантов несколько.

    Копия логики report_cli._resolve_place: скрипты намеренно самостоятельные,
    общий помощник живёт в services/charts.py и тянет за собой базу.
    """
    found = search_cities(query)
    if not found:
        sys.exit(f"Город не найден: {query!r}")

    if len(found) > 1:
        print(f"«{query}»: выбран отмеченный, другой — через --pick/--pick-b:", file=sys.stderr)
        for i, city in enumerate(found, 1):
            mark = "→" if i == pick else " "
            print(
                f" {mark} {i}. {city.display_name} ({city.country}), нас. {city.population:,}, {city.tz}",
                file=sys.stderr,
            )
        print(file=sys.stderr)

    if not 1 <= pick <= len(found):
        sys.exit(f"--pick {pick}: вариантов всего {len(found)}")
    return found[pick - 1]


def _side(date_str: str, time_str: str, city_query: str, pick: int) -> tuple[dict, bool, str]:
    """Карта одной стороны: (расчёт, известно ли время, подпись для шапки)."""
    try:
        day = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        sys.exit(f"Дата {date_str!r}: ожидается ГГГГ-ММ-ДД")

    time_known = time_str != UNKNOWN_MARK
    if time_known:
        try:
            moment = datetime.strptime(time_str, "%H:%M").time()
        except ValueError:
            sys.exit(f"Время {time_str!r}: ожидается ЧЧ:ММ или «{UNKNOWN_MARK}»")
    else:
        moment = UNKNOWN_TIME

    city = _resolve_city(city_query, pick)
    natal = compute_natal(datetime.combine(day, moment), city.lat, city.lon, city.tz)
    shown = time_str if time_known else f"{UNKNOWN_TIME:%H:%M} (время неизвестно)"
    return natal, time_known, f"{date_str} {shown} {city.display_name}, {city.tz}"


def _out_path(out_dir: Path, stamp: str, version: str, args: argparse.Namespace, run: int) -> Path:
    """Имя файла прогона: метка времени, версия промпта, связь, даты, номер.

    Метка времени одна на весь запуск — три прогона ложатся рядом и
    сортируются вместе, а вчерашняя приёмка не затирается сегодняшней.
    """
    name = f"{stamp}__{version}__{args.kind}__{args.date_a}+{args.date_b}__run{run}.txt"
    return out_dir / name


def _header(
    args: argparse.Namespace,
    where_a: str,
    where_b: str,
    version: str,
    temperature: float,
    run: int,
    result: client.LLMResult,
) -> str:
    """Шапка файла: при каких настройках получен текст.

    Без неё через три дня не ответить, почему один прогон вышел живым, а
    другой ватным, — а это главный вопрос приёмки.
    """
    usage = result.usage
    return "\n".join(
        [
            f"# {version}, kind={args.kind}, temperature={temperature}, модель {result.model}",
            f"# A: {where_a}",
            f"# B: {where_b}",
            (
                f"# прогон {run} из {args.runs}, токены: промпт {usage.prompt_tokens} "
                f"(из кеша {usage.cached_prompt_tokens}), ответ {usage.completion_tokens}"
            ),
            "",
            "",
        ]
    )


def main() -> None:
    args = _parse_args()

    module = PROMPTS[args.prompt]
    version: str = module.VERSION
    temperature = args.temperature
    if temperature is None:
        temperature = getattr(module, "TEMPERATURE", client.DEFAULT_TEMPERATURE)
    max_tokens = getattr(module, "MAX_TOKENS", 4096)

    natal_a, known_a, where_a = _side(args.date_a, args.time_a, args.city_a, args.pick)
    natal_b, known_b, where_b = _side(args.date_b, args.time_b, args.city_b, args.pick_b)

    calc = compute_synastry(natal_a, natal_b, a_time_known=known_a, b_time_known=known_b)
    user_message = module.build_user(calc, kind=args.kind, name_a=args.name_a, name_b=args.name_b)

    print(f"A: {where_a}", file=sys.stderr)
    print(f"B: {where_b}", file=sys.stderr)
    print(
        f"Контактов {len(calc['aspects'])}, в промпт идут "
        f"{len(calc['summary']['top']) if args.prompt == 'pair_full' else pair_free.TOP_ASPECTS}"
        f"; промпт {version}",
        file=sys.stderr,
    )

    if args.show_prompt:
        print("--- system ---")
        print(module.SYSTEM)
        print("--- user ---")
        print(user_message)
        return

    out_dir = None if args.no_save else args.out
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")

    print(
        f"Модель {args.model}, temperature {temperature}, max_tokens {max_tokens}, "
        f"прогонов {args.runs}\n",
        file=sys.stderr,
    )

    async def _run() -> None:
        try:
            for run in range(1, args.runs + 1):
                try:
                    result = await client.chat(
                        module.SYSTEM,
                        user_message,
                        model=args.model,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                except Exception as e:  # noqa: BLE001
                    # Один упавший прогон не должен уносить остальные: три
                    # генерации подряд — это минуты, и начинать заново обидно.
                    print(f"Прогон {run} не удался: {e}", file=sys.stderr)
                    continue

                # pair_full отвечает JSON-секциями (этого требует пейволл) —
                # читать его в терминале невозможно, поэтому модуль сам
                # приводит свой ответ к тексту.
                render = getattr(module, "render", None)
                text = render(result.text) if render else result.text

                if args.runs > 1:
                    print(f"\n{'=' * 70}\nПрогон {run} из {args.runs}\n{'=' * 70}")
                print(text)

                if out_dir is not None:
                    path = _out_path(out_dir, stamp, version, args, run)
                    header = _header(args, where_a, where_b, version, temperature, run, result)
                    path.write_text(header + text + "\n", encoding="utf-8")
                    print(f"→ {path}", file=sys.stderr)

                usage = result.usage
                print(
                    f"[{result.model}] промпт {usage.prompt_tokens} ток. "
                    f"(из кеша {usage.cached_prompt_tokens}), ответ {usage.completion_tokens} ток.",
                    file=sys.stderr,
                )
        finally:
            await client.close_client()

    asyncio.run(_run())


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LLM-разбор совместимости, без базы и кеша")
    parser.add_argument("date_a", help="дата рождения стороны A, ГГГГ-ММ-ДД")
    parser.add_argument("time_a", help=f"время A, ЧЧ:ММ или «{UNKNOWN_MARK}», если неизвестно")
    parser.add_argument("city_a", help="город рождения A")
    parser.add_argument("date_b", help="дата рождения стороны B")
    parser.add_argument("time_b", help=f"время B, ЧЧ:ММ или «{UNKNOWN_MARK}»")
    parser.add_argument("city_b", help="город рождения B")
    parser.add_argument("--pick", type=int, default=1, help="номер города A из подсказок")
    parser.add_argument("--pick-b", type=int, default=1, help="номер города B из подсказок")
    parser.add_argument(
        "--kind",
        required=True,
        choices=KINDS,
        help="тип связи: он идёт в промпт и заметно меняет текст",
    )
    parser.add_argument("--name-a", help="имя стороны A (в тексте к ней обращаются на «ты»)")
    parser.add_argument("--name-b", help="имя стороны B")
    parser.add_argument(
        "--prompt",
        choices=sorted(PROMPTS),
        default="pair_free",
        help="какой промпт использовать (по умолчанию тизер pair_free)",
    )
    parser.add_argument("--model", default=client.DEFAULT_MODEL, help="модель DeepSeek")
    parser.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="температура (по умолчанию из промпт-модуля)",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="сколько раз сгенерировать один и тот же разбор (приёмка — три)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"каталог для файлов прогонов (по умолчанию {DEFAULT_OUT})",
    )
    parser.add_argument("--no-save", action="store_true", help="не писать файлы, только stdout")
    parser.add_argument(
        "--show-prompt",
        action="store_true",
        help="напечатать system/user и выйти, не тратя токены",
    )

    args = parser.parse_args()
    if args.runs < 1:
        sys.exit("--runs: нужно хотя бы один прогон")
    return args


if __name__ == "__main__":
    main()
