"""Синастрия: аспекты между двумя натальными картами и признаки пары.

Вход — два готовых расчёта из compute_natal (те же словари, что лежат в
charts.calc["natal"]), выход — один dict. Ни базы, ни сети, ни эфемерид:
позиции планет уже посчитаны и сверены с astro-seek золотыми тестами, а
межкартный аспект — это разность долгот.

Орбы здесь СВОИ и заметно уже натальных, которые задаёт kerykeion: контакт
между картами с орбом 9° не ощущается никем. Обоснование и таблица —
в docs/astro-context.md, раздел про орбы синастрии.

Кроме списка аспектов считаем summary — производные признаки пары
(взаимность, пустоты, перекос вовлечённости). Они нужны промпту: словарная
трактовка отдельного аспекта одинакова у всех, а эти числа различают пары.
"""

from __future__ import annotations

from datetime import datetime

from astro_core.natal import ASPECTS_RU
from astro_core.transits import ASPECT_ANGLES

# Версия правил расчёта: орбы, состав точек, формула веса. Входит в
# calc_version пары — по ней видно, какие пары пересчитать после правки.
VERSION = "syn1"

# Точки, участвующие в синастрии, в порядке вывода.
#
# Узла и Хирона здесь нет намеренно. Узел у нас истинный, у большинства
# калькуляторов средний, расхождение до 1.5° — scripts/seek_check.py по этой
# причине его не сверяет, а непроверяемая точка в платном тексте недопустима.
# Вдобавок узлы тянут текст в «вы встретились не случайно», а фатальные
# формулировки запрещены правилами тона.
ACTIVE_POINTS = (
    "Sun",
    "Moon",
    "Mercury",
    "Venus",
    "Mars",
    "Jupiter",
    "Saturn",
    "Uranus",
    "Neptune",
    "Pluto",
    "Ascendant",
    "Medium_Coeli",
)

# Оси карты. Выделены отдельно: их орб зажат жёстче остальных (см. AXIS_ORB)
# и они выпадают из расчёта, когда время рождения неизвестно.
_AXES = ("Ascendant", "Medium_Coeli")

_LUMINARIES = ("Sun", "Moon")

# Базовый орб по типу аспекта. Соединение и оппозиция шире секстиля потому,
# что работают на большем удалении; секстиль слабейший из мажорных и на 5°
# уже неотличим от отсутствия аспекта.
ORBS = {
    "conjunction": 7.0,
    "opposition": 7.0,
    "trine": 6.0,
    "square": 6.0,
    "sextile": 3.0,
}

# Прибавка, когда хотя бы одна сторона аспекта — Солнце или Луна: тот же угол
# между Сатурном и Нептуном уже не читается, а со светилом читается.
LUMINARY_BONUS = 2.0

# Потолок для аспектов к Асценденту и MC, перебивает всё остальное:
# 4 минуты ошибки во времени рождения дают 1° по Асценденту.
AXIS_ORB = 3.0

# Вес точки в паре. Высшие планеты идут по знаку целыми поколениями, и
# «его Плутон в квадрате к её Венере» есть почти у всех ровесников — признак,
# общий для когорты, различает пары плохо.
_POINT_WEIGHT = {
    "Sun": 1.0,
    "Moon": 1.0,
    "Ascendant": 0.9,
    "Venus": 0.9,
    "Mars": 0.9,
    "Mercury": 0.7,
    "Saturn": 0.7,
    "Medium_Coeli": 0.7,
    "Jupiter": 0.6,
    "Uranus": 0.4,
    "Neptune": 0.4,
    "Pluto": 0.4,
}

_ASPECT_WEIGHT = {
    "conjunction": 1.0,
    "opposition": 0.9,
    "square": 0.85,
    "trine": 0.8,
    "sextile": 0.5,
}

# Гармоничные и напряжённые. Соединение не входит ни в те, ни в другие: его
# характер решает пара планет, а не сам аспект (Венера с Луной и Марс с
# Сатурном соединяются одинаково, а живётся с ними по-разному).
_SOFT = ("trine", "sextile")
_HARD = ("square", "opposition")

_ELEMENTS = {
    "fire": ("Ari", "Leo", "Sag"),
    "earth": ("Tau", "Vir", "Cap"),
    "air": ("Gem", "Lib", "Aqu"),
    "water": ("Can", "Sco", "Pis"),
}

_MODES = {
    "cardinal": ("Ari", "Can", "Lib", "Cap"),
    "fixed": ("Tau", "Leo", "Sco", "Aqu"),
    "mutable": ("Gem", "Vir", "Sag", "Pis"),
}

_SIGN_ELEMENT = {sign: name for name, signs in _ELEMENTS.items() for sign in signs}
_SIGN_MODE = {sign: name for name, signs in _MODES.items() for sign in signs}

# Точки, чьи знаки идут в summary отдельной строкой: именно ими торгует
# ручной рынок ВК, и промпту они нужны без вычислений.
_SIGNS_REPORTED = ("Sun", "Moon", "Venus", "Mars")

# Сколько аспектов уходит в промпт. Пара даёт 21-43 аспекта (замер по золотым
# картам), и списком это превращается в перечисление вместо текста.
TOP_LIMIT = 12

_TOP_TIE = 1e-9


def _separation(deg_a: float, deg_b: float) -> float:
    """Расстояние между долготами по короткой дуге, 0..180.

    Считаем через abs(a - b), а не через transits._norm180: тот для x и -x
    даёт разные последние биты, и орб уезжал на 0.01° при перестановке
    сторон местами. Здесь порядок аргументов не влияет на результат вовсе.
    """
    gap = abs(deg_a - deg_b) % 360.0
    return min(gap, 360.0 - gap)


def _points(natal: dict, *, time_known: bool) -> list[dict]:
    """Точки одной карты, готовые к перебору.

    При неизвестном времени Асцендент и MC выбрасываются: compute_natal
    считает их от подставленных 12:00, и в calc они выглядят настоящими.
    """
    out = [p for p in natal["planets"] if p["name"] in ACTIVE_POINTS]
    if time_known:
        out += [natal["angles"]["asc"], natal["angles"]["mc"]]
    return [p for p in out if p["name"] in ACTIVE_POINTS]


def _orb_limit(name_a: str, name_b: str, aspect: str) -> float:
    """Предельный орб для этой пары точек и этого аспекта."""
    limit = ORBS[aspect]
    if name_a in _LUMINARIES or name_b in _LUMINARIES:
        limit += LUMINARY_BONUS
    # Ось перебивает прибавку светила: Солнце на Асценденте с орбом 9°
    # означало бы разговор о времени рождения, которого мы не знаем.
    if name_a in _AXES or name_b in _AXES:
        limit = min(limit, AXIS_ORB)
    return limit


def _aspect_between(point_a: dict, point_b: dict) -> dict | None:
    """Аспект между двумя точками разных карт, None — если его нет.

    Возвращает самый тесный из подходящих: на широких орбах со светилами
    один и тот же угол может попасть сразу в два типа.
    """
    gap = _separation(point_a["abs_deg"], point_b["abs_deg"])
    best: dict | None = None

    for aspect, angle in ASPECT_ANGLES.items():
        orb = abs(gap - angle)
        limit = _orb_limit(point_a["name"], point_b["name"], aspect)
        if orb > limit:
            continue
        if best is not None and orb >= best["orb"]:
            continue

        weight = (
            _POINT_WEIGHT[point_a["name"]]
            * _POINT_WEIGHT[point_b["name"]]
            * _ASPECT_WEIGHT[aspect]
            * (1.0 - orb / limit)
        )
        best = {
            "a": point_a["name"],
            "a_ru": point_a["name_ru"],
            "b": point_b["name"],
            "b_ru": point_b["name_ru"],
            "type": aspect,
            "type_ru": ASPECTS_RU[aspect],
            "orb": round(orb, 2),
            "limit": round(limit, 1),
            "weight": round(weight, 4),
        }
    return best


def _distribution(natal: dict, table: dict[str, str]) -> dict[str, int]:
    """Сколько планет карты попало в каждую стихию (или крест).

    Углы не считаем: Асцендент есть не у всех карт, и с ним стихии двух
    сторон перестали бы сравниваться между собой.
    """
    out = dict.fromkeys(table.values(), 0)
    for planet in natal["planets"]:
        if planet["name"] in ACTIVE_POINTS:
            out[table[planet["sign"]]] += 1
    return out


def _key_signs(natal: dict) -> dict[str, str]:
    """Знаки Солнца, Луны, Венеры и Марса — по-русски, для промпта."""
    return {p["name"]: p["sign_ru"] for p in natal["planets"] if p["name"] in _SIGNS_REPORTED}


def _age_gap_years(natal_a: dict, natal_b: dict) -> float:
    """Разница возрастов в годах. Различает сюжеты: аспекты к высшим
    планетам у ровесников и у пары со сдвигом в 12 лет означают разное."""
    born_a = datetime.fromisoformat(natal_a["meta"]["birth_local"])
    born_b = datetime.fromisoformat(natal_b["meta"]["birth_local"])
    return round(abs((born_a - born_b).days) / 365.25, 1)


def _reciprocal(aspects: list[dict]) -> list[dict]:
    """Взаимные контакты: X одного к Y другого И одновременно Y одного к X другого.

    Свойство пары, а не аспекта, — в учебниках его нет, и есть оно далеко
    не у всех. Один из главных различителей для промпта.
    """
    seen = {(a["a"], a["b"]) for a in aspects}
    out = []
    for aspect in aspects:
        mirror = (aspect["b"], aspect["a"])
        # Пару берём один раз: иначе оба направления попадут в список
        if mirror in seen and (aspect["a"], aspect["b"]) < mirror:
            out.append(
                {
                    "points": [aspect["a"], aspect["b"]],
                    "points_ru": [aspect["a_ru"], aspect["b_ru"]],
                }
            )
    return out


def _voids(points: list[dict], aspects: list[dict], side: str) -> list[dict]:
    """Точки стороны, не получившие ни одного контакта.

    Отсутствия различают пары лучше присутствий, а сама модель их не
    заметит: она видит список того, что есть.
    """
    touched = {a[side] for a in aspects}
    return [
        {"point": p["name"], "point_ru": p["name_ru"]} for p in points if p["name"] not in touched
    ]


def _involvement(points: list[dict], aspects: list[dict], side: str) -> dict:
    """Насколько вовлечена сторона: сколько её точек задействовано и насколько важных.

    Перекос между сторонами — числовой факт; что он означает, решает промпт.

    touched_weight складывает веса ЗАТРОНУТЫХ ТОЧЕК, а не веса аспектов:
    сумма аспектных весов у обеих сторон одинакова по построению (каждый
    аспект принадлежит обеим), и как признак асимметрии она бесполезна.
    """
    touched = {a[side] for a in aspects}
    return {
        "points_touched": len(touched),
        "points_total": len(points),
        "touched_weight": round(sum(_POINT_WEIGHT[name] for name in touched), 2),
    }


def _harmony(aspects: list[dict]) -> float | None:
    """Доля веса гармоничных аспектов среди окрашенных, None — если их нет.

    Соединения в расчёт не идут (см. _SOFT/_HARD): их характер задаёт пара
    планет. None означает «пара состоит из одних соединений» — редкий, но
    возможный случай, и подменять его нулём нельзя.
    """
    soft = sum(a["weight"] for a in aspects if a["type"] in _SOFT)
    hard = sum(a["weight"] for a in aspects if a["type"] in _HARD)
    total = soft + hard
    if total <= _TOP_TIE:
        return None
    return round(soft / total, 2)


def compute_synastry(
    natal_a: dict,
    natal_b: dict,
    *,
    a_time_known: bool,
    b_time_known: bool,
) -> dict:
    """Считает синастрию по двум результатам compute_natal.

    a_time_known / b_time_known обязательны и без значения по умолчанию:
    в calc["natal"]["angles"] Асцендент и MC лежат всегда, даже когда время
    рождения неизвестно и посчитаны они от подставленных 12:00. Отличить их
    от настоящих по самому расчёту нельзя — флаг живёт только в колонке
    charts.time_known, и молчаливый дефолт означал бы аспекты к выдуманной оси.

    Стороны не симметричны: «a» — тот, для кого разбор (инициатор пары),
    «b» — партнёр. Венера первого к Марсу второго и Венера второго к Марсу
    первого — разные аспекты с разным смыслом, и склеивать их нельзя.
    """
    points_a = _points(natal_a, time_known=a_time_known)
    points_b = _points(natal_b, time_known=b_time_known)

    aspects = [
        aspect
        for pa in points_a
        for pb in points_b
        if (aspect := _aspect_between(pa, pb)) is not None
    ]
    aspects.sort(key=lambda a: -a["weight"])

    counts = {name: 0 for name in ASPECT_ANGLES}
    for aspect in aspects:
        counts[aspect["type"]] += 1

    return {
        "meta": {
            "version": VERSION,
            "orbs": dict(ORBS),
            "luminary_bonus": LUMINARY_BONUS,
            "axis_orb": AXIS_ORB,
            "points": list(ACTIVE_POINTS),
            "a": {"time_known": a_time_known, "axes_used": a_time_known},
            "b": {"time_known": b_time_known, "axes_used": b_time_known},
        },
        "aspects": aspects,
        "summary": {
            "counts": counts,
            "harmony": _harmony(aspects),
            "top": aspects[:TOP_LIMIT],
            "reciprocal": _reciprocal(aspects),
            "voids": {
                "a": _voids(points_a, aspects, "a"),
                "b": _voids(points_b, aspects, "b"),
            },
            "involvement": {
                "a": _involvement(points_a, aspects, "a"),
                "b": _involvement(points_b, aspects, "b"),
            },
            "elements": {
                "a": _distribution(natal_a, _SIGN_ELEMENT),
                "b": _distribution(natal_b, _SIGN_ELEMENT),
            },
            "modes": {
                "a": _distribution(natal_a, _SIGN_MODE),
                "b": _distribution(natal_b, _SIGN_MODE),
            },
            "signs": {"a": _key_signs(natal_a), "b": _key_signs(natal_b)},
            "age_gap_years": _age_gap_years(natal_a, natal_b),
        },
    }
