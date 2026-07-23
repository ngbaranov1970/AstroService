"""Геокодинг: офлайн-база городов GeoNames + timezonefinder.

Два назначения:
1. Подсказки города в онбординге: search_cities("Москв") → топ городов
   по населению; ищем и по-русски (включая исторические имена вроде
   «Свердловск»), и по-латински.
2. Таймзона для расчёта: у каждого города из базы tz уже есть (колонка
   GeoNames); для произвольных координат — tz_at(lat, lon) через timezonefinder.

Про исторические таймзоны: мы возвращаем ИМЯ зоны IANA (например,
"Europe/Moscow"), а не смещение. Сдвиг для конкретной даты рождения
(декретное время, отмены DST и т.п.) достаёт из базы tzdata сам расчётный
слой (kerykeion/zoneinfo) — поэтому «таймзона по координатам И дате»
получается автоматически, считать смещение руками здесь нельзя.

Файл данных backend/data/cities.txt собирается скриптом
scripts/build_cities.py (GeoNames cities5000 + русские названия из
alternateNamesV2). Путь переопределяется переменной ASTRO_CITIES_PATH.
"""

from __future__ import annotations

import csv
import os
from bisect import bisect_left
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from timezonefinder import TimezoneFinder

DATA_PATH = Path(
    os.environ.get(
        "ASTRO_CITIES_PATH",
        Path(__file__).resolve().parent.parent / "data" / "cities.txt",
    )
)

# Колонки cities.txt (формат задаёт scripts/build_cities.py, HEADER там же).
_COL_NAME = 1
_COL_NAME_RU = 2
_COL_ALT_RU = 3
_COL_COUNTRY = 4
_COL_LAT = 5
_COL_LON = 6
_COL_POPULATION = 7
_COL_TZ = 8


@dataclass(frozen=True, slots=True)
class City:
    name: str  # английское название (GeoNames name)
    name_ru: str | None  # русское название из alternateNamesV2
    country: str  # ISO-код страны ("RU", "KZ", …)
    lat: float
    lon: float
    population: int
    tz: str  # имя зоны IANA, напр. "Europe/Moscow"

    @property
    def display_name(self) -> str:
        return self.name_ru or self.name


def _norm(s: str) -> str:
    return s.strip().lower().replace("ё", "е")


class GeoDB:
    """Все города в памяти + отсортированный индекс имён для префиксного поиска.

    ~70 тыс. городов, грузится меньше секунды и живёт как синглтон
    на процесс (см. _db ниже).
    """

    def __init__(self, path: Path = DATA_PATH):
        if not path.exists():
            raise FileNotFoundError(
                f"База городов не найдена: {path}. "
                "Соберите её: python scripts/build_cities.py"
            )
        self.cities: list[City] = []
        keys: list[tuple[str, int]] = []

        with open(path, encoding="utf-8", newline="") as f:
            for row in csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE):
                idx = len(self.cities)
                self.cities.append(
                    City(
                        name=row[_COL_NAME],
                        name_ru=row[_COL_NAME_RU] or None,
                        country=row[_COL_COUNTRY],
                        lat=float(row[_COL_LAT]),
                        lon=float(row[_COL_LON]),
                        population=int(row[_COL_POPULATION]),
                        tz=row[_COL_TZ],
                    )
                )
                # Ключи поиска: латиница, русское имя и его синонимы
                # (в т.ч. исторические — «Свердловск» найдёт Екатеринбург).
                names = {_norm(row[_COL_NAME]), _norm(row[_COL_NAME_RU])}
                names.update(_norm(a) for a in row[_COL_ALT_RU].split(","))
                keys.extend((k, idx) for k in names if k)

        keys.sort()
        self._keys = keys

    def search(self, query: str, limit: int = 8) -> list[City]:
        """Города, чьё имя начинается с query; крупные — первыми."""
        q = _norm(query)
        if len(q) < 2:
            return []
        lo = bisect_left(self._keys, (q,))
        hi = bisect_left(self._keys, (q + "￿",))
        found = {idx: self.cities[idx] for _, idx in self._keys[lo:hi]}
        return sorted(found.values(), key=lambda c: -c.population)[:limit]


@lru_cache(maxsize=1)
def _db() -> GeoDB:
    return GeoDB()


@lru_cache(maxsize=1)
def _tf() -> TimezoneFinder:
    return TimezoneFinder()


def search_cities(query: str, limit: int = 8) -> list[City]:
    """Подсказки для поля «город рождения» в онбординге."""
    return _db().search(query, limit)


def tz_at(lat: float, lon: float) -> str:
    """Имя зоны IANA по координатам — для точек не из базы городов."""
    tz = _tf().timezone_at(lat=lat, lng=lon)
    if tz is None:
        raise ValueError(f"Не удалось определить таймзону для ({lat}, {lon})")
    return tz
