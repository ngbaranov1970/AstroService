"""Золотые тесты натальной карты (жёсткое требование проекта: сверка эталонов).

Каждый файл tests/golden/*.json — карта, посчитанная compute_natal и сверенная
глазами с astro-seek (планеты, углы, куспиды — совпадение в пределах минуты
дуги). Тест пересчитывает карту из meta эталона и сравнивает с записанным:
ловим регрессии от обновлений kerykeion/pyswisseph/tzdata.

Добавить эталон: сверить карту с astro-seek, затем
    python scripts/natal_cli.py ДАТА ВРЕМЯ ГОРОД --json > tests/golden/ИМЯ.json
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from astro_core.natal import compute_natal

GOLDEN_DIR = Path(__file__).parent / "golden"
GOLDEN_FILES = sorted(GOLDEN_DIR.glob("*.json"))

# Допуск на положение: 0.01° = 36 секунд дуги — обновление эфемерид может
# сдвинуть доли секунды, но знак, дом и минута должны стоять на месте.
DEG_TOL = 0.01


@pytest.mark.parametrize("path", GOLDEN_FILES, ids=lambda p: p.stem)
def test_golden_chart(path: Path) -> None:
    golden = json.loads(path.read_text(encoding="utf-8"))
    meta = golden["meta"]

    chart = compute_natal(
        birth_dt=datetime.fromisoformat(meta["birth_local"]),
        lat=meta["lat"],
        lon=meta["lon"],
        tz=meta["tz"],
    )

    assert chart["meta"]["julian_day"] == pytest.approx(meta["julian_day"])

    for got, exp in zip(chart["planets"], golden["planets"], strict=True):
        where = f"{path.stem}: {exp['name']}"
        assert got["name"] == exp["name"], where
        assert got["sign"] == exp["sign"], where
        assert got["house"] == exp["house"], where
        assert got["retrograde"] == exp["retrograde"], where
        assert got["abs_deg"] == pytest.approx(exp["abs_deg"], abs=DEG_TOL), where

    for key in ("asc", "mc"):
        got, exp = chart["angles"][key], golden["angles"][key]
        assert got["sign"] == exp["sign"], f"{path.stem}: {key}"
        assert got["abs_deg"] == pytest.approx(exp["abs_deg"], abs=DEG_TOL), f"{path.stem}: {key}"

    for got, exp in zip(chart["houses"], golden["houses"], strict=True):
        where = f"{path.stem}: дом {exp['num']}"
        assert got["sign"] == exp["sign"], where
        assert got["abs_deg"] == pytest.approx(exp["abs_deg"], abs=DEG_TOL), where


def test_golden_files_exist() -> None:
    """Пустой каталог эталонов — это «тесты прошли», чего быть не должно."""
    assert GOLDEN_FILES, "Нет ни одного эталона в tests/golden/"
