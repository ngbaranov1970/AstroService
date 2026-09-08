"""Ручная проверка синастрии: посчитать пару и напечатать в терминал.

Тот же путь, которым потом пойдёт app/services/pairs.py: два города →
две карты → compute_synastry. Нужен для сверки таблицы аспектов глазами
с калькулятором синастрии astro-seek и для вычитки промптов пары.

Сверять с astro-seek можно только те аспекты, что попадают в оба набора
орбов: наши орбы уже натальных (см. astro_core/synastry.py), и расхождение
по краю — не ошибка.

Примеры:
    python scripts/synastry_cli.py 1990-05-11 17:20 Касимов 1992-01-10 08:15 Москва
    python scripts/synastry_cli.py 1990-05-11 17:20 Касимов 1992-01-10 08:15 Москва --pick2 2
    python scripts/synastry_cli.py 1990-05-11 17:20 Касимов 1992-01-10 - Москва --unknown-b
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, time
from pathlib import Path

# Скрипт запускают из каталога backend; добавляем его в путь импорта,
# чтобы astro_core находился без установки пакета.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from astro_core.geo import City, search_cities  # noqa: E402
from astro_core.natal import compute_natal  # noqa: E402
from astro_core.synastry import compute_synastry  # noqa: E402

# То же, что services/charts.UNKNOWN_TIME: полдень как середина суток —
# дома и Асцендент обходят круг за сутки, промах держится в пределах 12 часов.
UNKNOWN_TIME = time(12, 0)


def _resolve_city(name: str, pick: int) -> City:
    """Город по имени, с выбором из подсказок. Ошибку печатает и выходит."""
    found = search_cities(name)
    if not found:
        sys.exit(f"Город не найден: {name!r}")

    if len(found) > 1:
        # В stderr, чтобы при `--json > файл` список не испортил JSON.
        print(f"«{name}»: выбран отмеченный, другой — через --pick/--pick2:", file=sys.stderr)
        for i, c in enumerate(found, 1):
            mark = "→" if i == pick else " "
            print(f" {mark} {i}. {c.display_name} ({c.country}), {c.tz}", file=sys.stderr)
        print(file=sys.stderr)

    if not 1 <= pick <= len(found):
        sys.exit(f"--pick {pick}: вариантов всего {len(found)}")
    return found[pick - 1]


def _build_chart(date_str: str, time_str: str, city: City, *, time_known: bool) -> dict:
    """Натальная карта одной стороны из строк аргументов."""
    moment = UNKNOWN_TIME if not time_known else datetime.strptime(time_str, "%H:%M").time()
    birth_dt = datetime.combine(datetime.strptime(date_str, "%Y-%m-%d").date(), moment)
    return compute_natal(birth_dt, city.lat, city.lon, city.tz)


def _print_side(label: str, city: City, chart: dict, *, time_known: bool) -> None:
    when = chart["meta"]["birth_local"] if time_known else "время неизвестно"
    print(f"{label}: {city.display_name} ({city.country})   {when}   tz {chart['meta']['tz']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Синастрия пары в терминале")
    parser.add_argument("date_a", help="дата рождения первого, ГГГГ-ММ-ДД")
    parser.add_argument("time_a", help="местное время первого, ЧЧ:ММ (или - при --unknown-a)")
    parser.add_argument("city_a", help="город рождения первого")
    parser.add_argument("date_b", help="дата рождения второго, ГГГГ-ММ-ДД")
    parser.add_argument("time_b", help="местное время второго, ЧЧ:ММ (или - при --unknown-b)")
    parser.add_argument("city_b", help="город рождения второго")
    parser.add_argument("--pick", type=int, default=1, help="номер города первого из подсказок")
    parser.add_argument("--pick2", type=int, default=1, help="номер города второго из подсказок")
    parser.add_argument("--unknown-a", action="store_true", help="время первого неизвестно")
    parser.add_argument("--unknown-b", action="store_true", help="время второго неизвестно")
    parser.add_argument(
        "--json",
        action="store_true",
        help="вывести расчёт как JSON (для эталона в tests/golden/synastry/)",
    )
    args = parser.parse_args()

    a_known = not args.unknown_a
    b_known = not args.unknown_b

    city_a = _resolve_city(args.city_a, args.pick)
    city_b = _resolve_city(args.city_b, args.pick2)

    natal_a = _build_chart(args.date_a, args.time_a, city_a, time_known=a_known)
    natal_b = _build_chart(args.date_b, args.time_b, city_b, time_known=b_known)

    result = compute_synastry(natal_a, natal_b, a_time_known=a_known, b_time_known=b_known)

    if args.json:
        import json

        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    _print_side("Первый", city_a, natal_a, time_known=a_known)
    _print_side("Второй", city_b, natal_b, time_known=b_known)

    s = result["summary"]
    aspects = result["aspects"]

    print(f"\n— Аспекты ({len(aspects)}), по весу —")
    for i, x in enumerate(aspects, 1):
        # Верхушку списка помечаем: ровно она уходит в промпт.
        mark = "*" if i <= len(s["top"]) else " "
        print(
            f" {mark} {x['weight']:.3f}  {x['a_ru']:<14} {x['type_ru']:<10} {x['b_ru']:<14}"
            f"  орб {x['orb']:>5}° из {x['limit']}°"
        )
    print("  * — уходит в промпт")

    print("\n— Сводка —")
    print(f"  Типы: {s['counts']}")
    print(f"  Гармония: {s['harmony']}   Разница возрастов: {s['age_gap_years']} г.")
    print(f"  Стихии:  первый {s['elements']['a']}   второй {s['elements']['b']}")
    print(f"  Кресты:  первый {s['modes']['a']}   второй {s['modes']['b']}")
    print(f"  Вовлечённость: первый {s['involvement']['a']}   второй {s['involvement']['b']}")

    for side, label in (("a", "первого"), ("b", "второго")):
        empty = ", ".join(v["point_ru"] for v in s["voids"][side]) or "нет"
        print(f"  Без контактов у {label}: {empty}")

    pairs = ", ".join(" ↔ ".join(p["points_ru"]) for p in s["reciprocal"]) or "нет"
    print(f"  Взаимные контакты: {pairs}")


if __name__ == "__main__":
    main()
