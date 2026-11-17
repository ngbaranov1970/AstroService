"""Личные добавки к карте: чужие точки в её домах, транзиты, Луна за сутки.

Вход — готовые расчёты compute_natal и даты, выход — списки dict'ов. Ни базы,
ни сети. Нужны черновику разбора пары (app/services/vip_pair.py): наложение
планет партнёра на дома, прогноз по медленным планетам и честная оговорка
про Луну человека без времени рождения.

Эфемериды и поиск нулей берём у transits.py, включая его служебные функции:
второй экземпляр _refine и флагов swisseph разошёлся бы с первым.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from astro_core import transits as _t
from astro_core.natal import ASPECTS_RU, PLANETS_RU

# Планеты, чьи транзиты длятся неделями и годами. Быстрые (Солнце…Марс)
# повторяются у каждого каждый год — в прогнозе на полтора года это шум.
SLOW_BODIES = ("Jupiter", "Saturn", "Uranus", "Neptune", "Pluto")

# Смещения, на которых аспект транзита точен. Трин и секстиль не считаем:
# событий они не дают, а список удлиняют втрое.
_TRANSIT_OFFSETS = {
    "conjunction": (0.0,),
    "opposition": (180.0,),
    "square": (90.0, -90.0),
}

# Точки карты, к которым считаем транзиты: личные планеты и углы.
_TARGETS = ("Sun", "Moon", "Mercury", "Venus", "Mars")

# Десять планет без узла и Хирона — состав тот же, что в синастрии.
_OVERLAY_POINTS = tuple(_t._BODIES)


def house_of(abs_deg: float, houses: list[dict]) -> int:
    """Номер дома карты, в который попадает долгота abs_deg.

    houses — natal["houses"]. Дом тянется от своего куспида до следующего;
    точка ровно на куспиде относится к дому, который с него начинается.
    """
    cusps = sorted(houses, key=lambda h: h["num"])
    for i, house in enumerate(cusps):
        nxt = cusps[(i + 1) % len(cusps)]
        width = (nxt["abs_deg"] - house["abs_deg"]) % 360.0
        if (abs_deg - house["abs_deg"]) % 360.0 < width:
            return house["num"]
    # Недостижимо при двенадцати куспидах, покрывающих круг; ошибка лучше дома «None».
    raise ValueError(f"долгота {abs_deg} не попала ни в один дом")


def overlay(host: dict, guest: dict) -> list[dict]:
    """Планеты карты guest в домах карты host: «его Солнце в её 8-м доме».

    Зовут только при известном времени рождения host: его дома от
    подставленного полудня выдуманы, и отличить их по расчёту нельзя.
    """
    return [
        {"name": p["name"], "name_ru": p["name_ru"], "house": house_of(p["abs_deg"], host["houses"])}
        for p in guest["planets"]
        if p["name"] in _OVERLAY_POINTS
    ]


def sky_in_houses(natal: dict, day: date) -> list[dict]:
    """Медленные планеты на дату day в домах карты: «Сатурн сейчас в 8-м доме»."""
    jd = _t._to_jd(_t._day_start(day))
    out = []
    for body in SLOW_BODIES:
        lon = _t._lon_speed(jd, body)[0]
        out.append(
            {
                "body": body,
                "body_ru": PLANETS_RU[body],
                "house": house_of(lon, natal["houses"]),
                **_t._sign_fields(lon),
            }
        )
    return out


def transits_to(natal: dict, start: date, end: date, *, time_known: bool) -> list[dict]:
    """Точные транзиты медленных планет к точкам карты за интервал дат.

    Один элемент — одна тройка «планета, аспект, точка» со всеми датами
    точного аспекта: из-за попятного хода их бывает до трёх. Даты московские.
    Без времени рождения нет углов и нет Луны: за сутки она проходит до 13°,
    и дата транзита Плутона к ней уехала бы на годы.
    """
    points = [p for p in natal["planets"] if p["name"] in _TARGETS]
    if time_known:
        points += [natal["angles"]["asc"], natal["angles"]["mc"]]
    else:
        points = [p for p in points if p["name"] != "Moon"]

    jd_start = _t._to_jd(_t._day_start(start))
    days = (end - start).days + 1
    grid = [jd_start + i for i in range(days + 1)]

    out: list[dict] = []
    for body in SLOW_BODIES:
        # Долготы по суточной сетке считаем один раз на планету, а не на каждую цель.
        lons = [_t._lon_speed(jd, body)[0] for jd in grid]
        for point in points:
            for aspect, offsets in _TRANSIT_OFFSETS.items():
                dates: list[date] = []
                for offset in offsets:
                    target = point["abs_deg"] + offset
                    dates += _crossings(body, target, grid, lons)
                if dates:
                    out.append(
                        {
                            "body": body,
                            "body_ru": PLANETS_RU[body],
                            "aspect": aspect,
                            "aspect_ru": ASPECTS_RU[aspect],
                            "point": point["name"],
                            "point_ru": point["name_ru"],
                            "dates": sorted(dates),
                        }
                    )
    out.sort(key=lambda item: item["dates"][0])
    return out


def _crossings(body: str, target: float, grid: list[float], lons: list[float]) -> list[date]:
    """Даты, когда долгота тела проходит через target (в любую сторону)."""

    def gap(jd: float) -> float:
        return _t._norm180(_t._lon_speed(jd, body)[0] - target)

    found = []
    gaps = [_t._norm180(lon - target) for lon in lons]
    for i in range(len(grid) - 1):
        prev, cur = gaps[i], gaps[i + 1]
        # Скачок больше 90° за сутки — разрыв на ±180, а не проход через цель.
        if (prev <= 0) != (cur <= 0) and abs(cur - prev) < 90.0:
            found.append(_t._to_msk(_t._refine(gap, grid[i], grid[i + 1])).date())
    return found


def moon_span(birth: date, tz: str) -> dict:
    """Луна в начале и в конце суток рождения по местному времени.

    Для человека без времени рождения: same_sign=True — знак Луны известен
    точно; иначе change_at — местное время «ЧЧ:ММ», когда она сменила знак.
    """
    zone = ZoneInfo(tz)
    local_start = datetime(birth.year, birth.month, birth.day, tzinfo=zone)
    jd_start = _t._to_jd(local_start)
    jd_end = _t._to_jd(local_start + timedelta(days=1))

    first = _t._sign_fields(_t._lon_speed(jd_start, "Moon")[0])
    last = _t._sign_fields(_t._lon_speed(jd_end, "Moon")[0])
    change_at = None
    if first["sign"] != last["sign"]:
        # Шаг в целые сутки безопасен: Луна проходит 13°, границу знака пересечёт один раз.
        jd = next(_t._scan_sign_change("Moon", jd_start, jd_end, jd_end - jd_start))
        change_at = _t._to_msk(jd).astimezone(zone).strftime("%H:%M")
    return {
        "start": first,
        "end": last,
        "same_sign": first["sign"] == last["sign"],
        "change_at": change_at,
    }
