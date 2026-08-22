"""Проверки календаря событий (astro_core/transits.py).

Ловим два класса поломок. Первый — расхождение с реальностью: даты затмений и
лунаций сверены с публичными эталонами (NASA eclipse catalog, timeanddate),
и обновление pyswisseph не должно их сдвигать. Второй — ошибки самого поиска
нулей: пропущенный корень или ложный корень на разрыве угла ±180.

Эталонные моменты — максимум затмения и точная фаза, время московское.
Допуск 5 минут: у «максимума затмения» несколько определений, и источники
расходятся на минуты, а нам важны дата и час.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from astro_core import transits as t
from astro_core.natal import compute_natal

# Допуск на положение тот же, что в золотых тестах натальной карты.
DEG_TOL = 0.01

def _msk(*args: int) -> datetime:
    """Момент в московской зоне: эталоны сравниваются с aware-датами модуля."""
    return datetime(*args, tzinfo=t.MSK)


# (дата, вид, тип, момент максимума в МСК)
ECLIPSES_2026 = [
    (date(2026, 2, 17), "solar_eclipse", "кольцеобразное", _msk(2026, 2, 17, 15, 12)),
    (date(2026, 8, 12), "solar_eclipse", "полное", _msk(2026, 8, 12, 20, 46)),
    (date(2026, 3, 3), "lunar_eclipse", "полное", _msk(2026, 3, 3, 14, 34)),
    (date(2026, 8, 28), "lunar_eclipse", "частное", _msk(2026, 8, 28, 7, 13)),
]

LUNATIONS_2026 = [
    ("full_moon", _msk(2026, 8, 28, 7, 18)),
    ("new_moon", _msk(2026, 9, 11, 6, 27)),
]

# Вход Солнца в кардинальные знаки — это равноденствия и солнцестояния,
# самые проверяемые даты года. Ошибись поиск границы знака — здесь он виден
# сразу, без астрологических источников вообще.
SOLAR_INGRESSES_2026 = [
    ("Ari", _msk(2026, 3, 20, 17, 46)),
    ("Can", _msk(2026, 6, 21, 11, 25)),
    ("Lib", _msk(2026, 9, 23, 3, 5)),
]

TOL = timedelta(minutes=5)


@pytest.mark.parametrize("day, kind, eclipse_kind, peak", ECLIPSES_2026,
                         ids=[e[0].isoformat() for e in ECLIPSES_2026])
def test_eclipse_matches_reference(day, kind, eclipse_kind, peak) -> None:
    found = [e for e in t.eclipses(day, day) if e["kind"] == kind]
    assert len(found) == 1, f"ожидалось одно затмение {day}, найдено {len(found)}"

    event = found[0]
    assert event["eclipse_kind"] == eclipse_kind
    assert abs(event["moment"] - peak) <= TOL, event["moment"]


@pytest.mark.parametrize("kind, moment", LUNATIONS_2026, ids=[l[0] for l in LUNATIONS_2026])
def test_lunation_matches_reference(kind, moment) -> None:
    day = moment.date()
    found = [e for e in t.lunations(day, day) if e["kind"] == kind]
    assert len(found) == 1

    assert abs(found[0]["moment"] - moment) <= TOL, found[0]["moment"]


@pytest.mark.parametrize("sign, moment", SOLAR_INGRESSES_2026,
                         ids=[s[0] for s in SOLAR_INGRESSES_2026])
def test_solar_ingress_matches_equinox(sign, moment) -> None:
    """Вход Солнца в кардинальный знак = равноденствие или солнцестояние."""
    day = moment.date()
    found = [e for e in t.ingresses(day, day) if e["body"] == "Sun"]
    assert len(found) == 1

    assert found[0]["sign"] == sign
    assert abs(found[0]["moment"] - moment) <= TOL, found[0]["moment"]


def test_positions_match_natal_core() -> None:
    """Прямой swisseph обязан совпадать с ядром карты, сверенным по astro-seek.

    Это сильнее внешней сверки: расходись они, натальная карта и календарь
    называли бы разные знаки в один и тот же день.
    """
    moment = _msk(2026, 8, 22, 12, 0)
    chart = compute_natal(moment.replace(tzinfo=None), 55.7558, 37.6173, "Europe/Moscow")
    jd = t._to_jd(moment)

    for planet in chart["planets"]:
        if planet["name"] not in t._BODIES:
            continue
        lon, _ = t._lon_speed(jd, planet["name"])
        assert lon == pytest.approx(planet["abs_deg"], abs=DEG_TOL), planet["name"]


def test_lunar_eclipse_happens_at_full_moon() -> None:
    """Лунное затмение бывает только в полнолуние — проверка связности поиска."""
    day = date(2026, 8, 28)
    eclipse = next(e for e in t.eclipses(day, day) if e["kind"] == "lunar_eclipse")
    full_moon = next(e for e in t.lunations(day, day) if e["kind"] == "full_moon")

    assert abs(eclipse["moment"] - full_moon["moment"]) < timedelta(hours=1)


def test_solar_eclipse_happens_at_new_moon() -> None:
    day = date(2026, 8, 12)
    eclipse = next(e for e in t.eclipses(day, day) if e["kind"] == "solar_eclipse")
    new_moon = next(e for e in t.lunations(day, day) if e["kind"] == "new_moon")

    assert abs(eclipse["moment"] - new_moon["moment"]) < timedelta(hours=1)


def test_station_actually_reverses_motion() -> None:
    """До станции и после неё планета идёт в разные стороны."""
    stations = t.stations(date(2026, 2, 20), date(2026, 3, 25))
    mercury = [e for e in stations if e["body"] == "Mercury"]
    assert len(mercury) == 2, "у Меркурия здесь разворот туда и обратно"

    for event in mercury:
        jd = t._to_jd(event["moment"])
        before = t._lon_speed(jd - 1.0, "Mercury")[1]
        after = t._lon_speed(jd + 1.0, "Mercury")[1]
        assert (before > 0) != (after > 0), event["title_ru"]
        assert (after > 0) == (event["kind"] == "station_direct")


def test_ingress_lands_on_sign_border() -> None:
    """В момент ингресса тело стоит в самом начале нового знака."""
    events = t.ingresses(date(2026, 9, 1), date(2026, 9, 30))
    assert events

    for event in events:
        jd = t._to_jd(event["moment"])
        lon_before = t._lon_speed(jd - 0.02, event["body"])[0]
        lon_after = t._lon_speed(jd + 0.02, event["body"])[0]
        assert t._sign_index(lon_before) != t._sign_index(lon_after), event["title_ru"]
        assert t._sign_index(lon_after) == t._SIGN_CODES.index(event["sign"])


def test_moon_is_not_in_ingresses() -> None:
    """Луна меняет знак каждые 2.5 дня — в ленте это шум, её там быть не должно."""
    events = t.ingresses(date(2026, 9, 1), date(2026, 9, 30))
    assert all(e["body"] != "Moon" for e in events)


def test_exact_aspect_is_exact() -> None:
    """В найденный момент угол между телами равен углу аспекта."""
    events = t.exact_aspects(date(2026, 8, 22), date(2026, 9, 10))
    assert events

    for event in events:
        jd = t._to_jd(event["moment"])
        gap = abs(t._norm180(
            t._lon_speed(jd, event["body"])[0] - t._lon_speed(jd, event["body2"])[0]
        ))
        assert gap == pytest.approx(t.ASPECT_ANGLES[event["aspect"]], abs=0.01), event["title_ru"]


def test_events_are_sorted_and_inclusive() -> None:
    """Границы интервала входят в него, события идут по времени."""
    start, end = date(2026, 8, 22), date(2026, 8, 28)
    events = t.events_between(start, end)
    assert events

    moments = [e["moment"] for e in events]
    assert moments == sorted(moments)
    assert all(start <= m.date() <= end for m in moments)

    # Полнолуние 28.08 стоит последним днём интервала: отбрось мы верхнюю
    # границу — оно бы пропало, и это самая частая ошибка в таких выборках.
    assert any(e["kind"] == "full_moon" for e in events)


def test_day_sky_shape() -> None:
    sky = t.day_sky(date(2026, 8, 22))

    assert len(sky["positions"]) == len(t._BODIES)
    known_phases = {p[1] for p in t._PHASES_RU} | {p[1] for p in t._PHASES_BETWEEN_RU}
    assert sky["moon"]["phase_ru"] in known_phases
    assert [a["orb"] for a in sky["aspects"]] == sorted(a["orb"] for a in sky["aspects"])
    assert any(p["name"] == "Saturn" and p["retrograde"] for p in sky["positions"])


def test_every_lunation_day_is_named_by_its_phase() -> None:
    """В день лунации фаза обязана называться своим именем, а не промежутком.

    Орб считается от фактической скорости Луны: возьми мы фиксированные 6°,
    лунация с точным моментом около полуночи быстрого месяца оказалась бы
    дальше орба от обоих соседних полудней и не подписалась бы нигде.
    """
    expected = {"new_moon": "новолуние", "full_moon": "полнолуние"}
    lunations = t.lunations(date(2026, 1, 1), date(2026, 6, 30))
    assert len(lunations) >= 12

    for event in lunations:
        day = event["moment"].date()
        assert t.day_sky(day)["moon"]["phase_ru"] == expected[event["kind"]], day


def test_phase_between_quarters_is_not_a_quarter() -> None:
    """23.08.2026 — три дня после первой четверти, и назвать её так нельзя."""
    moon = t.day_sky(date(2026, 8, 23))["moon"]
    assert moon["phase_ru"] == "прибывающая Луна"
    assert moon["growing"] is True


def test_titles_agree_in_gender() -> None:
    """Заголовок уходит прямо в промпт — грамматику там уже не поправят."""
    stations = t.stations(date(2026, 10, 1), date(2026, 11, 30))
    venus = [e for e in stations if e["body"] == "Venus"]
    assert venus

    for event in venus:
        assert "ретроградной" in event["title_ru"] or "директной" in event["title_ru"], (
            event["title_ru"]
        )

    for event in t.ingresses(date(2026, 9, 1), date(2026, 9, 10)):
        assert "входит в знак" in event["title_ru"]
