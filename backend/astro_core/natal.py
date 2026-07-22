"""Натальная карта: планеты, дома (Placidus), аспекты.

Ядро не знает про FastAPI, базу и VK: вход — дата/время рождения, координаты
и таймзона, выход — dict, который passport.py собирает в «астропаспорт».
Таймзону сюда передают уже вычисленную (см. geo.py) — исторические таймзоны
решаются там, а не здесь.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from kerykeion import AstrologicalSubject, NatalAspects

SIGNS_RU = {
    "Ari": "Овен",
    "Tau": "Телец",
    "Gem": "Близнецы",
    "Can": "Рак",
    "Leo": "Лев",
    "Vir": "Дева",
    "Lib": "Весы",
    "Sco": "Скорпион",
    "Sag": "Стрелец",
    "Cap": "Козерог",
    "Aqu": "Водолей",
    "Pis": "Рыбы",
}

PLANETS_RU = {
    "Sun": "Солнце",
    "Moon": "Луна",
    "Mercury": "Меркурий",
    "Venus": "Венера",
    "Mars": "Марс",
    "Jupiter": "Юпитер",
    "Saturn": "Сатурн",
    "Uranus": "Уран",
    "Neptune": "Нептун",
    "Pluto": "Плутон",
    "True_North_Lunar_Node": "Северный узел",
    "Chiron": "Хирон",
    "Ascendant": "Асцендент",
    "Medium_Coeli": "Середина неба",
}

ASPECTS_RU = {
    "conjunction": "соединение",
    "opposition": "оппозиция",
    "trine": "трин",
    "square": "квадрат",
    "sextile": "секстиль",
}

# Атрибуты AstrologicalSubject в порядке вывода в паспорт.
_PLANET_ATTRS = [
    "sun",
    "moon",
    "mercury",
    "venus",
    "mars",
    "jupiter",
    "saturn",
    "uranus",
    "neptune",
    "pluto",
    "true_north_lunar_node",
    "chiron",
]

_HOUSE_ATTRS = [
    "first_house",
    "second_house",
    "third_house",
    "fourth_house",
    "fifth_house",
    "sixth_house",
    "seventh_house",
    "eighth_house",
    "ninth_house",
    "tenth_house",
    "eleventh_house",
    "twelfth_house",
]

_HOUSE_NUM = {name: i + 1 for i, name in enumerate(
    [
        "First_House",
        "Second_House",
        "Third_House",
        "Fourth_House",
        "Fifth_House",
        "Sixth_House",
        "Seventh_House",
        "Eighth_House",
        "Ninth_House",
        "Tenth_House",
        "Eleventh_House",
        "Twelfth_House",
    ]
)}


def _point(p: Any) -> dict:
    """Переводчик между объектами библиотеки kerykeion и нашим JSON-форматом
    Одна точка карты (планета или угол) в плоский dict для JSONB."""
    return {
        "name": p.name,
        "name_ru": PLANETS_RU.get(p.name, p.name),
        "sign": p.sign,
        "sign_ru": SIGNS_RU[p.sign],
        "deg_in_sign": round(p.position, 4),
        "abs_deg": round(p.abs_pos, 4),
        "house": _HOUSE_NUM.get(getattr(p, "house", None)),
        "retrograde": bool(getattr(p, "retrograde", False)),
    }


def compute_natal(birth_dt: datetime, lat: float, lon: float, tz: str) -> dict:
    """Считает натальную карту.

    birth_dt — локальное время рождения (naive), tz — имя зоны IANA.
    Дома — Placidus, зодиак тропический (дефолты kerykeion/swisseph).
    """
    subject = AstrologicalSubject(
        name="natal",
        year=birth_dt.year,
        month=birth_dt.month,
        day=birth_dt.day,
        hour=birth_dt.hour,
        minute=birth_dt.minute,
        lat=lat,
        lng=lon,
        tz_str=tz,
        city="-",
        nation="-",
        online=False,
    )

    planets = [_point(getattr(subject, attr)) for attr in _PLANET_ATTRS]

    angles = {
        "asc": _point(subject.first_house) | {"name": "Ascendant", "name_ru": PLANETS_RU["Ascendant"]},
        "mc": _point(subject.tenth_house) | {"name": "Medium_Coeli", "name_ru": PLANETS_RU["Medium_Coeli"]},
    }

    houses = [
        {
            "num": i + 1,
            "sign": h.sign,
            "sign_ru": SIGNS_RU[h.sign],
            "deg_in_sign": round(h.position, 4),
            "abs_deg": round(h.abs_pos, 4),
        }
        for i, h in enumerate(getattr(subject, attr) for attr in _HOUSE_ATTRS)
    ]

    aspects = [
        {
            "p1": a.p1_name,
            "p1_ru": PLANETS_RU.get(a.p1_name, a.p1_name),
            "p2": a.p2_name,
            "p2_ru": PLANETS_RU.get(a.p2_name, a.p2_name),
            "type": a.aspect,
            "type_ru": ASPECTS_RU.get(a.aspect, a.aspect),
            "orb": round(a.orbit, 2),
        }
        for a in NatalAspects(subject).relevant_aspects
        if a.aspect in ASPECTS_RU and a.p1_name in PLANETS_RU and a.p2_name in PLANETS_RU
    ]

    return {
        "meta": {
            "birth_local": birth_dt.isoformat(),
            "tz": tz,
            "lat": lat,
            "lon": lon,
            "julian_day": subject.julian_day,
            "houses_system": "Placidus",
            "zodiac": "tropical",
        },
        "planets": planets,
        "angles": angles,
        "houses": houses,
        "aspects": aspects,
    }
