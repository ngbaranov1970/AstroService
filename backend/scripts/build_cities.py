"""Сборка компактной базы городов для geo.py (запускается один раз).

Проблема: в cities5000.txt синонимы городов идут без кода языка, поэтому
«первое кириллическое» название может оказаться осетинским или башкирским
(«Мæскуы» вместо «Москва»). Языковые метки есть только в alternateNamesV2.

Что делает скрипт:
1. Берёт cities5000.zip (все города мира с населением 5000+) и
   alternateNamesV2.zip (все синонимы с кодами языков) — скачивает
   в кеш-каталог, если файлов ещё нет.
2. Для каждого города выбирает русское название: среди строк с
   isolanguage == "ru" предпочитает помеченное isPreferredName и не
   историческое; исторические (Свердловск, Ленинград) сохраняет отдельно —
   по ним тоже можно будет искать.
3. Пишет backend/data/cities.txt — TSV с фиксированными колонками
   (см. HEADER ниже), которые читает astro_core/geo.py.

Запуск:  python scripts/build_cities.py [каталог_с_zip]
"""

from __future__ import annotations

import csv
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

BASE_URL = "https://download.geonames.org/export/dump/"
CITIES_ZIP = "cities5000.zip"
ALTNAMES_ZIP = "alternateNamesV2.zip"

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OUT_PATH = DATA_DIR / "cities.txt"

# Формат выходного файла (TSV без заголовка, порядок колонок фиксирован).
HEADER = ["geonameid", "name", "name_ru", "alt_ru", "country", "lat", "lon", "population", "tz"]


def _fetch(cache_dir: Path, filename: str) -> Path:
    path = cache_dir / filename
    if not path.exists():
        print(f"Скачиваю {BASE_URL}{filename} …")
        urllib.request.urlretrieve(BASE_URL + filename, path)
    return path


def _read_cities(zip_path: Path) -> list[list[str]]:
    """Строки cities5000.txt: geonameid, name, ascii, altnames, lat, lon, …"""
    with zipfile.ZipFile(zip_path) as z, z.open(zip_path.stem + ".txt") as f:
        text = io.TextIOWrapper(f, encoding="utf-8", newline="")
        return list(csv.reader(text, delimiter="\t", quoting=csv.QUOTE_NONE))


def _read_ru_names(zip_path: Path, wanted_ids: set[str]) -> dict[str, dict]:
    """geonameid → {"preferred": имя|None, "names": [все ru-имена]}.

    Колонки alternateNamesV2: alternateNameId, geonameid, isolanguage,
    alternate name, isPreferredName, isShortName, isColloquial, isHistoric, …
    Файл большой (~600 МБ), поэтому стримим построчно прямо из zip.
    """
    ru: dict[str, dict] = {}
    with zipfile.ZipFile(zip_path) as z, z.open("alternateNamesV2.txt") as f:
        for line in io.TextIOWrapper(f, encoding="utf-8"):
            row = line.rstrip("\n").split("\t")
            if row[2] != "ru" or row[1] not in wanted_ids:
                continue
            entry = ru.setdefault(row[1], {"preferred": None, "names": []})
            name = row[3]
            entry["names"].append(name)
            is_preferred = len(row) > 4 and row[4] == "1"
            is_historic = len(row) > 7 and row[7] == "1"
            if is_preferred and not is_historic:
                entry["preferred"] = name
    return ru


def main() -> None:
    cache_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DATA_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    cities = _read_cities(_fetch(cache_dir, CITIES_ZIP))
    print(f"Городов: {len(cities)}")

    ru = _read_ru_names(_fetch(cache_dir, ALTNAMES_ZIP), {row[0] for row in cities})
    print(f"…из них с русскими названиями: {len(ru)}")

    with open(OUT_PATH, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", quoting=csv.QUOTE_NONE, quotechar=None)
        for row in cities:
            gid = row[0]
            entry = ru.get(gid, {"preferred": None, "names": []})
            name_ru = entry["preferred"] or (entry["names"][0] if entry["names"] else "")
            # Остальные ru-имена (в т.ч. исторические) — как ключи поиска.
            alt_ru = ",".join(n for n in dict.fromkeys(entry["names"]) if n != name_ru)
            w.writerow(
                [gid, row[1], name_ru, alt_ru, row[8], row[4], row[5], row[14] or "0", row[17]]
            )

    print(f"Готово: {OUT_PATH} ({OUT_PATH.stat().st_size / 1e6:.1f} МБ)")


if __name__ == "__main__":
    main()
