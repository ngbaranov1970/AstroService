"""Проверки личных добавок к карте (astro_core/personal.py).

Дома сверяются с kerykeion по золотым картам, транзиты — с ингрессами из
transits.py, которые уже сверены с эталонами: проход планеты через 0° Овна
и есть её вход в Овен.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from astro_core import personal
from astro_core import transits as t

GOLDEN_FILES = sorted((Path(__file__).parent / "golden").glob("*.json"))


def _golden(name: str) -> dict:
    return json.loads((Path(__file__).parent / "golden" / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", GOLDEN_FILES, ids=[p.stem for p in GOLDEN_FILES])
def test_house_of_matches_kerykeion(path: Path) -> None:
    golden = json.loads(path.read_text(encoding="utf-8"))
    for planet in golden["planets"]:
        assert personal.house_of(planet["abs_deg"], golden["houses"]) == planet["house"], planet["name"]


def test_house_of_cusp_belongs_to_its_house() -> None:
    houses = _golden("1990-05-11_kasimov.json")["houses"]
    for house in houses:
        assert personal.house_of(house["abs_deg"], houses) == house["num"]


def test_overlay_of_chart_on_itself_is_its_own_houses() -> None:
    golden = _golden("1990-05-11_kasimov.json")
    own = {p["name"]: p["house"] for p in golden["planets"]}
    laid = personal.overlay(golden, golden)
    assert len(laid) == 10  # без узла и Хирона
    assert all(own[p["name"]] == p["house"] for p in laid)


def test_transit_to_aries_point_is_ingress() -> None:
    start, end = date(2025, 1, 1), date(2028, 12, 31)
    # Карта из одной точки в 0° Овна: соединение с ней — переход границы Рыбы/Овен.
    natal = {"planets": [{"name": "Sun", "name_ru": "Солнце", "abs_deg": 0.0}], "angles": {}}
    found = {
        x["body"]: x["dates"]
        for x in personal.transits_to(natal, start, end, time_known=False)
        if x["aspect"] == "conjunction"
    }
    for body in ("Saturn", "Neptune"):
        ingresses = [
            e["moment"].date() for e in t.ingresses(start, end)
            # Попятный возврат в Рыбы — тоже проход через 0° Овна.
            if e["body"] == body and e["sign"] in ("Ari", "Pis")
        ]
        assert found[body] == ingresses and len(ingresses) == 3


def test_transits_without_time_skip_moon_and_axes() -> None:
    natal = _golden("1990-05-11_kasimov.json")
    start, end = date(2020, 1, 1), date(2029, 12, 31)
    with_time = {x["point"] for x in personal.transits_to(natal, start, end, time_known=True)}
    no_time = {x["point"] for x in personal.transits_to(natal, start, end, time_known=False)}
    assert {"Moon", "Ascendant", "Medium_Coeli"} <= with_time
    assert not {"Moon", "Ascendant", "Medium_Coeli"} & no_time


def test_moon_span_sign_change() -> None:
    # 05.04.1987: Луна перешла из Близнецов в Рак ночью по московскому времени.
    span = personal.moon_span(date(1987, 4, 5), "Europe/Moscow")
    assert (span["start"]["sign"], span["end"]["sign"]) == ("Gem", "Can")
    assert not span["same_sign"] and span["change_at"] is not None
    # 13.07.1988: весь день в Раке.
    same = personal.moon_span(date(1988, 7, 13), "Europe/Moscow")
    assert same["same_sign"] and same["change_at"] is None and same["start"]["sign"] == "Can"
