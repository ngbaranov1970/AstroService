"""Тесты синастрии (жёсткое требование проекта: сверка расчётов).

Прямая сверка таблицы аспектов с astro-seek невозможна — у них своя таблица
орбов (об этом прямо сказано в шапке scripts/seek_check.py). Поэтому сверяем
иначе, в два слоя:

1. Позиции планет уже сверены с astro-seek золотыми картами, а синастрия —
   чистая функция от них. Значит проверять надо ровно то, что мы добавили:
   попадание в орб, выбор типа, сторону. Тест считает аспекты независимо,
   перебором в лоб, и сравнивает с модулем.
2. Свойства, которые обязаны выполняться на любой паре: симметрия сторон,
   исчезновение осей при неизвестном времени, границы весов.

Эталоны лежат в golden/synastry/ — подкаталогом, а не рядом с картами:
test_natal.py делает glob("*.json") по корню golden/ и параметризует тест
всеми файлами, так что эталон пары рядом с картами уронил бы золотые тесты.

Каждый эталон помнит в meta.source, из каких золотых карт он посчитан.
Пересоздать (после осознанной правки орбов или весов):

    python -c "
    import json; from pathlib import Path
    from astro_core.synastry import compute_synastry
    g = Path('tests/golden'); fa, fb, ka, kb = 'A.json', 'B.json', True, True
    a = json.loads((g / fa).read_text()); b = json.loads((g / fb).read_text())
    r = compute_synastry(a, b, a_time_known=ka, b_time_known=kb)
    r['meta']['source'] = {'a': fa, 'b': fb}
    print(json.dumps(r, ensure_ascii=False, indent=2))" > tests/golden/synastry/ИМЯ.json
"""

from __future__ import annotations

import json
from itertools import combinations
from pathlib import Path

import pytest

from astro_core.synastry import (
    ACTIVE_POINTS,
    AXIS_ORB,
    LUMINARY_BONUS,
    ORBS,
    TOP_LIMIT,
    compute_synastry,
)
from astro_core.transits import ASPECT_ANGLES

GOLDEN_DIR = Path(__file__).parent / "golden"
GOLDEN_FILES = sorted(GOLDEN_DIR.glob("*.json"))

_AXES = ("Ascendant", "Medium_Coeli")
_LUMINARIES = ("Sun", "Moon")

# Пары карт для параметризации. Все 45 сочетаний гоняются быстро (расчёта
# эфемерид здесь нет — только арифметика по готовым долготам).
PAIRS = list(combinations(GOLDEN_FILES, 2))
PAIR_IDS = [f"{a.stem}+{b.stem}" for a, b in PAIRS]


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _expected_limit(name_a: str, name_b: str, aspect: str) -> float:
    """Предельный орб, посчитанный независимо от модуля — по правилам из плана."""
    limit = ORBS[aspect]
    if name_a in _LUMINARIES or name_b in _LUMINARIES:
        limit += LUMINARY_BONUS
    if name_a in _AXES or name_b in _AXES:
        limit = min(limit, AXIS_ORB)
    return limit


def _brute_force(natal_a: dict, natal_b: dict) -> dict[tuple[str, str], tuple[str, float]]:
    """Аспекты, посчитанные в лоб: {(точка A, точка B): (тип, орб)}.

    Намеренно не использует ничего из synastry.py, кроме таблиц-констант:
    смысл теста в том, чтобы прийти к результату другим путём.
    """
    points_a = [p for p in natal_a["planets"] if p["name"] in ACTIVE_POINTS]
    points_a += [natal_a["angles"]["asc"], natal_a["angles"]["mc"]]
    points_b = [p for p in natal_b["planets"] if p["name"] in ACTIVE_POINTS]
    points_b += [natal_b["angles"]["asc"], natal_b["angles"]["mc"]]

    out: dict[tuple[str, str], tuple[str, float]] = {}
    for pa in points_a:
        for pb in points_b:
            # Расстояние между долготами по короткой дуге, 0..180
            raw = abs(pa["abs_deg"] - pb["abs_deg"]) % 360.0
            gap = min(raw, 360.0 - raw)
            best: tuple[str, float] | None = None
            for aspect, angle in ASPECT_ANGLES.items():
                orb = abs(gap - angle)
                if orb <= _expected_limit(pa["name"], pb["name"], aspect) and (
                    best is None or orb < best[1]
                ):
                    best = (aspect, orb)
            if best is not None:
                out[(pa["name"], pb["name"])] = best
    return out


@pytest.mark.parametrize(("path_a", "path_b"), PAIRS, ids=PAIR_IDS)
def test_aspects_match_brute_force(path_a: Path, path_b: Path) -> None:
    """Модуль находит ровно те же аспекты, что независимый перебор."""
    natal_a, natal_b = _load(path_a), _load(path_b)
    result = compute_synastry(natal_a, natal_b, a_time_known=True, b_time_known=True)

    got = {(a["a"], a["b"]): (a["type"], a["orb"]) for a in result["aspects"]}
    expected = _brute_force(natal_a, natal_b)

    assert set(got) == set(expected), "набор пар точек с аспектами разошёлся"
    for key, (aspect, orb) in expected.items():
        assert got[key][0] == aspect, f"{key}: тип аспекта"
        assert got[key][1] == pytest.approx(orb, abs=0.01), f"{key}: орб"


@pytest.mark.parametrize(("path_a", "path_b"), PAIRS, ids=PAIR_IDS)
def test_sides_are_symmetric(path_a: Path, path_b: Path) -> None:
    """Смена сторон местами даёт те же аспекты с переставленными сторонами.

    Ловит самую вероятную ошибку — перепутанные стороны: она невидима, пока
    смотришь на одну пару, и делает текст разбора неверным про конкретного
    человека.
    """
    natal_a, natal_b = _load(path_a), _load(path_b)
    straight = compute_synastry(natal_a, natal_b, a_time_known=True, b_time_known=True)
    swapped = compute_synastry(natal_b, natal_a, a_time_known=True, b_time_known=True)

    forward = {(x["a"], x["b"]): (x["type"], x["orb"]) for x in straight["aspects"]}
    backward = {(x["b"], x["a"]): (x["type"], x["orb"]) for x in swapped["aspects"]}
    assert forward == backward


@pytest.mark.parametrize(("path_a", "path_b"), PAIRS, ids=PAIR_IDS)
def test_unknown_time_drops_only_its_own_axes(path_a: Path, path_b: Path) -> None:
    """Без времени у стороны исчезают её оси, а точки второй остаются.

    Асцендент и MC при неизвестном времени compute_natal всё равно считает
    (от подставленных 12:00), и в calc они выглядят настоящими — значит
    выбрасывать их обязан именно этот модуль.
    """
    natal_a, natal_b = _load(path_a), _load(path_b)
    result = compute_synastry(natal_a, natal_b, a_time_known=True, b_time_known=False)

    assert not [x for x in result["aspects"] if x["b"] in _AXES], "оси второго остались"
    assert result["meta"]["b"]["axes_used"] is False

    # Оси первого при этом никуда не делись — проверяем, что выключение
    # адресное. Аспектов к ним может не быть вовсе (орб осей всего 3°),
    # поэтому смотрим на объявленный состав, а не на список аспектов.
    assert result["meta"]["a"]["axes_used"] is True


@pytest.mark.parametrize(("path_a", "path_b"), PAIRS, ids=PAIR_IDS)
def test_result_shape(path_a: Path, path_b: Path) -> None:
    """Границы значений и согласованность сводки — на любой паре."""
    natal_a, natal_b = _load(path_a), _load(path_b)
    result = compute_synastry(natal_a, natal_b, a_time_known=True, b_time_known=True)
    aspects, summary = result["aspects"], result["summary"]

    for x in aspects:
        assert x["orb"] <= x["limit"], f"орб больше предела: {x}"
        assert 0.0 <= x["weight"] <= 1.0, f"вес вне диапазона: {x}"
        assert x["a"] in ACTIVE_POINTS and x["b"] in ACTIVE_POINTS

    # Отсортировано по убыванию веса: на этом держится отбор в промпт
    assert [x["weight"] for x in aspects] == sorted((x["weight"] for x in aspects), reverse=True)

    assert sum(summary["counts"].values()) == len(aspects)
    assert summary["top"] == aspects[:TOP_LIMIT]
    if summary["harmony"] is not None:
        assert 0.0 <= summary["harmony"] <= 1.0

    for side, natal in (("a", natal_a), ("b", natal_b)):
        # Стихии и кресты считаются по одним и тем же планетам, без углов
        planets = len([p for p in natal["planets"] if p["name"] in ACTIVE_POINTS])
        assert sum(summary["elements"][side].values()) == planets
        assert sum(summary["modes"][side].values()) == planets

        touched = {x[side] for x in aspects}
        involvement = summary["involvement"][side]
        assert involvement["points_touched"] == len(touched)

        # Пустоты и затронутые точки в сумме дают все точки стороны
        voids = {v["point"] for v in summary["voids"][side]}
        assert not (voids & touched), "точка одновременно в пустотах и в контактах"
        assert len(voids) + len(touched) == involvement["points_total"]


def test_reciprocal_is_mutual() -> None:
    """Взаимными считаются только контакты, идущие в обе стороны."""
    natal_a = _load(GOLDEN_FILES[0])
    natal_b = _load(GOLDEN_FILES[1])
    result = compute_synastry(natal_a, natal_b, a_time_known=True, b_time_known=True)

    pairs = {(x["a"], x["b"]) for x in result["aspects"]}
    for item in result["summary"]["reciprocal"]:
        first, second = item["points"]
        assert (first, second) in pairs and (second, first) in pairs
        # Точка сама с собой взаимной парой быть не может: это один аспект
        assert first != second


def test_golden_synastry() -> None:
    """Эталоны из golden/synastry/ пересчитываются в то же самое.

    Ловит регрессии от правок орбов и весов: изменили таблицу — эталон
    перестал совпадать, и это повод осознанно его перезаписать, а не
    случайность.
    """
    golden_dir = GOLDEN_DIR / "synastry"
    files = sorted(golden_dir.glob("*.json")) if golden_dir.exists() else []
    if not files:
        pytest.skip("эталоны синастрии ещё не записаны")

    for path in files:
        golden = json.loads(path.read_text(encoding="utf-8"))
        meta = golden["meta"]
        natal_a = _load(GOLDEN_DIR / meta["source"]["a"])
        natal_b = _load(GOLDEN_DIR / meta["source"]["b"])

        result = compute_synastry(
            natal_a,
            natal_b,
            a_time_known=meta["a"]["time_known"],
            b_time_known=meta["b"]["time_known"],
        )
        assert result["aspects"] == golden["aspects"], path.stem
        assert result["summary"] == golden["summary"], path.stem
