"""Автосверка compute_natal с astro-seek: скачивает их расчёт и сравнивает.

Инструмент выполнения жёсткого требования проекта «сверка эталонных дат»:
им проверены 10 карт в tests/golden/ (см. docs/progress.md). Запускать
при добавлении новых эталонов или при сомнениях после обновления библиотек.

Как работает: строит URL расчёта astro-seek по координатам города (таймзону
astro-seek определяет сам — это и есть независимая проверка нашей цепочки
geo → tzdata), качает страницу curl-ом, парсит планеты/дома из HTML
(astro-seek отдаёт секунды дуги) и сравнивает с нашим compute_natal.
Допуск 0.021° ≈ 75 секунд дуги. Аспекты не сравниваются (другие орбы),
узел не сравнивается (у них средний, у нас истинный, разница до ~1.5°).

Запуск (из каталога backend):
    python scripts/seek_check.py 1990-05-11 17:20 Касимов
    python scripts/seek_check.py 1990-05-11 17:20 Москва 2   # --pick 2

Страницы кешируются в data/seek_cache/ (в git не попадает).
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from astro_core.geo import search_cities  # noqa: E402
from astro_core.natal import compute_natal  # noqa: E402

CACHE = Path(__file__).resolve().parent.parent / "data" / "seek_cache"
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"

PLANET_ICON = {
    "slunce": "Sun",
    "luna": "Moon",
    "merkur": "Mercury",
    "venuse": "Venus",
    "mars": "Mars",
    "jupiter": "Jupiter",
    "saturn": "Saturn",
    "uran": "Uranus",
    "neptun": "Neptune",
    "pluto": "Pluto",
}

SIGN3 = {
    "aries": "Ari", "taurus": "Tau", "gemini": "Gem", "cancer": "Can",
    "leo": "Leo", "virgo": "Vir", "libra": "Lib", "scorpio": "Sco",
    "sagittarius": "Sag", "capricorn": "Cap", "aquarius": "Aqu", "pisces": "Pis",
}

SIGN_DEG = {s: i * 30 for i, s in enumerate(SIGN3.values())}

# Планета: иконка → знак → 20&deg;35'23'' → дом («end of 2» — планета у самого
# конца дома) → Direct/Retrograde.
PLANET_RE = re.compile(
    r"planeta-velka-bila-([a-z]+)\.png.{0,300}?symbol_([a-z]+)_mobil_color.{0,400}?"
    r">(\d+)</span>&deg;<span[^>]*>(\d+)&rsquo;(\d+)&rsquo;&rsquo;</span></td>\n"
    r"<td[^>]*>\s*(?:end of\s*)?(\d+)</td>\n<td[^>]*>(Direct|Retrograde|Stationary[^<]*)",
    re.S,
)

# Куспид: строка начинается с номера дома («<a>1</a></strong>:» или
# «<span>4</span>:», у 5-го дома astro-seek не закрывает </td>), дальше знак
# (возможно с пометкой «(DESC)») и градусы-минуты-секунды.
HOUSE_RE = re.compile(
    r">(\d{1,2})</(?:a|span)>(?:</strong>)?:\s*(?:</td>)?.{0,300}?symbol_([a-z]+)_mobil_color.{0,400}?"
    r">(\d+)</span>&deg;<span[^>]*>(\d+)&rsquo;(\d+)",
    re.S,
)


def fetch(date: str, time: str, lat: float, lon: float) -> str:
    CACHE.mkdir(parents=True, exist_ok=True)
    cache = CACHE / f"seek_{date}_{lat:.2f}_{lon:.2f}.html"
    if cache.exists():
        return cache.read_text(encoding="utf-8")
    y, m, d = date.split("-")
    hh, mm = time.split(":")
    la, lo = abs(lat), abs(lon)
    url = (
        "https://horoscopes.astro-seek.com/calculate-birth-chart-horoscope-online/"
        f"?send_calculation=1&narozeni_den={int(d)}&narozeni_mesic={int(m)}&narozeni_rok={y}"
        f"&narozeni_hodina={int(hh)}&narozeni_minuta={int(mm)}"
        f"&narozeni_city=x&narozeni_mesto_hidden=x&narozeni_stat_hidden=&narozeni_podstat_kratky_hidden="
        f"&narozeni_sirka_stupne={int(la)}&narozeni_sirka_minuty={round((la % 1) * 60)}"
        f"&narozeni_sirka_smer={0 if lat >= 0 else 1}"
        f"&narozeni_delka_stupne={int(lo)}&narozeni_delka_minuty={round((lo % 1) * 60)}"
        f"&narozeni_delka_smer={0 if lon >= 0 else 1}"
        "&narozeni_timezone_form=auto&narozeni_timezone_dst_form=auto"
    )
    html = subprocess.run(
        ["curl", "-s", "-A", UA, "--max-time", "30", url],
        capture_output=True, text=True, check=True,
    ).stdout
    cache.write_text(html, encoding="utf-8")
    return html


def parse(html: str) -> tuple[dict, dict]:
    """→ (планеты {Sun: (abs_deg, house, retro)}, куспиды {1: abs_deg})."""
    planets: dict[str, tuple[float, int, bool]] = {}
    for icon, sign, dg, mi, sec, house, motion in PLANET_RE.findall(html):
        name = PLANET_ICON.get(icon)
        if name and name not in planets:
            deg = SIGN_DEG[SIGN3[sign]] + int(dg) + int(mi) / 60 + int(sec) / 3600
            planets[name] = (deg, int(house), motion.startswith("Retro"))
    houses: dict[int, float] = {}
    # Куспиды парсим только в секции домов, чтобы номера не цеплялись
    # к чужим таблицам.
    houses_html = html[html.find("#dum-1") :]
    for num, sign, dg, mi, sec in HOUSE_RE.findall(houses_html):
        n = int(num)
        if n not in houses:
            houses[n] = SIGN_DEG[SIGN3[sign]] + int(dg) + int(mi) / 60 + int(sec) / 3600
    return planets, houses


def check(date: str, time: str, city_q: str, pick: int = 1) -> bool:
    city = search_cities(city_q)[pick - 1]
    chart = compute_natal(
        datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M"), city.lat, city.lon, city.tz
    )
    seek_planets, seek_houses = parse(fetch(date, time, city.lat, city.lon))
    if len(seek_planets) < 10 or len(seek_houses) < 12:
        print(f"  !! распарсено планет {len(seek_planets)}, домов {len(seek_houses)} — парсер не сработал")
        return False

    ok = True
    tol = 0.021  # ~75 секунд дуги: допуск на разное усечение секунд

    print(f"\n=== {date} {time} {city.display_name} ({city.tz}) ===")
    for p in chart["planets"]:
        if p["name"] not in seek_planets:
            continue  # узел и Хирон не сверяем (см. докстринг)
        s_deg, s_house, s_retro = seek_planets[p["name"]]
        d = abs(p["abs_deg"] - s_deg)
        d = min(d, 360 - d)
        good = d <= tol and p["house"] == s_house and p["retrograde"] == s_retro
        ok &= good
        mark = "OK " if good else ">>> РАСХОЖДЕНИЕ"
        print(
            f"  {mark} {p['name']:<8} мы {p['abs_deg']:8.4f}° дом {p['house']:>2} {'R' if p['retrograde'] else ' '}"
            f" | seek {s_deg:8.4f}° дом {s_house:>2} {'R' if s_retro else ' '} | Δ{d * 60:.2f}'"
        )
    for h in chart["houses"]:
        s_deg = seek_houses[h["num"]]
        d = abs(h["abs_deg"] - s_deg)
        d = min(d, 360 - d)
        good = d <= tol
        ok &= good
        mark = "OK " if good else ">>> РАСХОЖДЕНИЕ"
        print(f"  {mark} дом {h['num']:>2}  мы {h['abs_deg']:8.4f}° | seek {s_deg:8.4f}° | Δ{d * 60:.2f}'")
    print("  ИТОГ:", "СОШЛОСЬ" if ok else "ЕСТЬ РАСХОЖДЕНИЯ")
    return ok


if __name__ == "__main__":
    date, time, city_q = sys.argv[1], sys.argv[2], sys.argv[3]
    pick = int(sys.argv[4]) if len(sys.argv) > 4 else 1
    check(date, time, city_q, pick)
