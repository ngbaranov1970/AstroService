"""Ручная проверка расчёта: посчитать натальную карту и напечатать в терминал.

Это первая живая склейка geo.py + natal.py — тот же путь, который потом
пройдёт app/services/charts.py: город → координаты и таймзона → расчёт.

Зачем: взять реальных людей, посчитать и сверить глазами с astro-seek
(градусы печатаем в формате «12°34'», как показывает astro-seek).
Подтверждённые карты потом лягут эталонами в tests/golden/.

Примеры:
    python scripts/natal_cli.py 1986-05-12 14:30 Свердловск
    python scripts/natal_cli.py 1990-01-01 03:15 "Нижний Нов"
    python scripts/natal_cli.py 1986-05-12 14:30 Москва --pick 2   # не первый город из подсказок
    python scripts/natal_cli.py 1986-05-12 14:30 --lat 56.83 --lon 60.6  # без базы городов
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

# Скрипт запускают из каталога backend; добавляем его в путь импорта,
# чтобы astro_core находился без установки пакета.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from astro_core.geo import City, search_cities, tz_at  # noqa: E402
from astro_core.natal import compute_natal  # noqa: E402


def _dms(deg: float) -> str:
    """27.7183 → "27°43'" — формат, в котором градусы показывает astro-seek."""
    d = int(deg)
    m = round((deg - d) * 60)
    if m == 60:  # округление 59.5+ минут перекатывается в следующий градус
        d, m = d + 1, 0
    return f"{d}°{m:02d}'"


def _resolve_place(args: argparse.Namespace) -> tuple[float, float, str, str]:
    """Возвращает (lat, lon, tz, подпись места) из --lat/--lon или по имени города."""
    if args.lat is not None and args.lon is not None:
        tz = args.tz or tz_at(args.lat, args.lon)
        return args.lat, args.lon, tz, f"({args.lat}, {args.lon})"

    if not args.city:
        sys.exit("Укажите город или пару --lat/--lon")

    found = search_cities(args.city)
    if not found:
        sys.exit(f"Город не найден: {args.city!r}")

    if len(found) > 1:
        # В stderr, чтобы при `--json > файл` список не испортил JSON.
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
    parser = argparse.ArgumentParser(description="Натальная карта в терминале")
    parser.add_argument("date", help="дата рождения, ГГГГ-ММ-ДД")
    parser.add_argument("time", help="местное время рождения, ЧЧ:ММ")
    parser.add_argument("city", nargs="?", help="город рождения (поиск по базе GeoNames)")
    parser.add_argument("--pick", type=int, default=1, help="номер города из подсказок")
    parser.add_argument("--lat", type=float, help="широта вместо города")
    parser.add_argument("--lon", type=float, help="долгота вместо города")
    parser.add_argument("--tz", help="зона IANA (иначе определяется по координатам)")
    parser.add_argument(
        "--json",
        action="store_true",
        help="вывести карту как JSON (для сохранения эталона в tests/golden/)",
    )
    args = parser.parse_args()

    birth_dt = datetime.strptime(f"{args.date} {args.time}", "%Y-%m-%d %H:%M")
    lat, lon, tz, place = _resolve_place(args)

    chart = compute_natal(birth_dt, lat, lon, tz)

    if args.json:
        import json

        print(json.dumps(chart, ensure_ascii=False, indent=2))
        return

    m = chart["meta"]
    print(f"Место: {place}   {m['lat']:.4f}, {m['lon']:.4f}   tz {m['tz']}")
    print(f"Время: {m['birth_local']} (местное)   JD {m['julian_day']:.5f}")
    print(f"Дома: {m['houses_system']}, зодиак: {m['zodiac']}")

    print("\n— Планеты —")
    for p in chart["planets"]:
        rx = " R" if p["retrograde"] else ""
        house = f"{p['house']:>2} дом" if p["house"] else "      "
        print(f"  {p['name_ru']:<14} {_dms(p['deg_in_sign']):>7} {p['sign_ru']:<8} {house}{rx}")

    print("\n— Углы —")
    for a in (chart["angles"]["asc"], chart["angles"]["mc"]):
        print(f"  {a['name_ru']:<14} {_dms(a['deg_in_sign']):>7} {a['sign_ru']}")

    print("\n— Куспиды домов —")
    for h in chart["houses"]:
        print(f"  {h['num']:>2}. {_dms(h['deg_in_sign']):>7} {h['sign_ru']}")

    print(f"\n— Аспекты ({len(chart['aspects'])}) —")
    for a in chart["aspects"]:
        print(f"  {a['p1_ru']} — {a['type_ru']} — {a['p2_ru']}  (орб {a['orb']}°)")


if __name__ == "__main__":
    main()
