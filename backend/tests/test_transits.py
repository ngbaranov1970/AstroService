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

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path

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


def test_illumination_agrees_with_lunations() -> None:
    """В новолуние диск тёмный, в полнолуние полный — сверка с самими лунациями.

    Внешний эталон здесь не нужен: моменты лунаций уже сверены выше, а
    освещённость приходит из другого вызова swisseph, чем долготы. Разойдись
    они — пост сообщил бы «диск освещён на 40 %» в день новолуния.
    """
    expected = {"new_moon": 0, "full_moon": 100}
    for event in t.lunations(date(2026, 1, 1), date(2026, 6, 30)):
        got = t.day_sky(event["moment"].date())["moon"]["illumination"]
        # Допуск 3 %: положения берутся на полдень, а лунация приходится на
        # любой час суток, и за полдня диск успевает приоткрыться.
        assert abs(got - expected[event["kind"]]) <= 3, (event["title_ru"], got)


def test_moon_aspect_to_sun_is_the_lunation() -> None:
    """Соединение Луны с Солнцем — это новолуние, оппозиция — полнолуние.

    Два независимых пути к одному моменту: moon_events ищет аспект Луна—тело,
    lunations — разность долгот через 0 и 180. Пропусти moon_events корень на
    своём шаге в шесть часов, здесь это видно сразу.
    """
    pairs = {"new_moon": "conjunction", "full_moon": "opposition"}
    for event in t.lunations(date(2026, 9, 1), date(2026, 10, 31)):
        found = [
            e for e in t.moon_events(event["moment"].date())
            if e["kind"] == "moon_aspect"
            and e["body2"] == "Sun"
            and e["aspect"] == pairs[event["kind"]]
        ]
        assert len(found) == 1, event["title_ru"]
        assert abs(found[0]["moment"] - event["moment"]) <= timedelta(minutes=1)


def test_moon_events_are_exact_and_complete() -> None:
    """Аспекты точны в свой момент, а ингрессы Луны за месяц никуда не делись.

    Полнота проверяется счётом: знак Луна проходит за 2.3 суток, значит за
    сентябрь смен знака 12-14. Меньше — сканирование потеряло корень.
    """
    events = [e for day in _september_2026() for e in t.moon_events(day)]
    ingresses = [e for e in events if e["kind"] == "moon_ingress"]
    assert 12 <= len(ingresses) <= 14, len(ingresses)

    for event in ingresses:
        jd = t._to_jd(event["moment"])
        before = t._sign_index(t._lon_speed(jd - 0.02, "Moon")[0])
        after = t._sign_index(t._lon_speed(jd + 0.02, "Moon")[0])
        assert before != after, event["title_ru"]
        assert after == t._SIGN_CODES.index(event["sign"])

    for event in (e for e in events if e["kind"] == "moon_aspect"):
        jd = t._to_jd(event["moment"])
        gap = abs(t._norm180(
            t._lon_speed(jd, "Moon")[0] - t._lon_speed(jd, event["body2"])[0]
        ))
        assert gap == pytest.approx(t.ASPECT_ANGLES[event["aspect"]], abs=0.01), (
            event["title_ru"]
        )


def test_moon_aspects_match_the_month_long_scan() -> None:
    """Посуточный поиск обязан дать те же аспекты, что сплошной за месяц.

    Проверка полноты, которой не даёт сверка «найденный момент точен»: она
    молчит о пропущенных. exact_aspects идёт по месяцу целиком и режет его
    своим шагом, moon_events — каждые сутки заново, и совпадение двух разных
    разбиений и есть доказательство, что ни один корень не потерян.
    """
    month = [e for day in _september_2026() for e in t.moon_events(day)
             if e["kind"] == "moon_aspect"]
    whole = [
        e for e in t.exact_aspects(date(2026, 9, 1), date(2026, 9, 30), include_moon=True)
        if "Moon" in (e["body"], e["body2"])
    ]
    assert len(whole) > 40, "за месяц Луна замыкает десятки аспектов, выборка мала"

    assert sorted(e["moment"] for e in month) == sorted(e["moment"] for e in whole)


def test_applying_flips_around_the_exact_moment() -> None:
    """До точного аспекта он сходится, после — расходится.

    Берём планетную пару, а не лунную: Луна выходит из орба за сутки, и
    аспекта в соседнем дне может уже не быть.
    """
    exact = t.exact_aspects(date(2026, 9, 10), date(2026, 9, 20))
    assert exact

    checked = 0
    for event in exact:
        day = event["moment"].date()
        for offset, applying in ((-1, True), (1, False)):
            same = [
                a for a in t.day_sky(day + timedelta(days=offset))["aspects"]
                if a["body"] == event["body"]
                and a["body2"] == event["body2"]
                and a["aspect"] == event["aspect"]
            ]
            if not same:
                continue
            assert same[0]["applying"] is applying, (event["title_ru"], offset)
            checked += 1
    assert checked, "не нашлось ни одной пары для проверки — тест ничего не проверил"


def test_orb_delta_agrees_with_applying_flag() -> None:
    """Флаг и число не могут спорить: applying — это ровно знак orb_delta_day."""
    for a in t.day_sky(date(2026, 9, 5))["aspects"]:
        assert a["applying"] is (a["orb_delta_day"] < 0), a


def test_next_lunation_is_the_first_one_ahead() -> None:
    """Ближайшая лунация — та же, что первая в списке; в день лунации это 0 дней."""
    for day in (date(2026, 9, 5), date(2026, 9, 11), date(2026, 9, 26)):
        got = t.day_sky(day)["next_lunation"]
        first = min(
            (e for e in t.lunations(day, day + timedelta(days=40))
             if e["moment"].date() >= day),
            key=lambda e: e["moment"],
        )
        assert got["moment"] == first["moment"], day
        assert got["days_until"] == (first["moment"].date() - day).days
        assert got["days_until"] >= 0


def test_moon_data_stays_out_of_events() -> None:
    """Луна не попала в sky["events"] — иначе изменятся тексты рассылки.

    Промпт daily_horoscope печатает events построчно, и добавка лунных
    событий переписала бы гороскоп, который уходит подписчикам в личку.
    Формат рассылки решено не трогать, поэтому проверка живёт в тестах.
    """
    for day in _september_2026():
        kinds = {e["kind"] for e in t.day_sky(day)["events"]}
        assert not kinds & {"moon_ingress", "moon_aspect"}, day


def _september_2026() -> list[date]:
    return [date(2026, 9, 1) + timedelta(days=i) for i in range(30)]


# --- Знак Солнца по одной дате ----------------------------------------------

GOLDEN_DIR = Path(__file__).parent / "golden"


@pytest.mark.parametrize("path", sorted(GOLDEN_DIR.glob("*.json")), ids=lambda p: p.stem)
def test_sun_sign_on_agrees_with_golden_chart(path: Path) -> None:
    """Знак по дате — тот же, что в сверенной карте, или отказ, но не чужой."""
    golden = json.loads(path.read_text(encoding="utf-8"))
    day = date.fromisoformat(golden["meta"]["birth_local"][:10])
    sun = next(p for p in golden["planets"] if p["name"] == "Sun")
    assert t.sun_sign_on(day) in (sun["sign_ru"], None)


def _sun_sign_by_natal(day: date, clock: time, tz: str) -> str:
    """Знак Солнца из ядра карты: момент на этой дате в этом поясе."""
    chart = compute_natal(datetime.combine(day, clock), 0.0, 0.0, tz)
    return next(p["sign_ru"] for p in chart["planets"] if p["name"] == "Sun")


def _solar_ingress_days_2026() -> list[date]:
    """Дни смены знака Солнцем в 2026 году и по дню с каждой стороны."""
    days = set()
    for e in t.ingresses(date(2026, 1, 1), date(2026, 12, 31)):
        if e["body"] == "Sun":
            days.update(e["moment"].date() + timedelta(days=d) for d in (-2, -1, 0, 1, 2))
    return sorted(days)


def test_sun_sign_on_matches_natal_core_at_window_edges() -> None:
    """Окно даты — от полуночи в UTC+14 до конца суток в UTC−12.

    Назвала функция знак — ядро карты даёт его на обоих краях окна. Отказала —
    края расходятся, то есть отказ не ложный.
    """
    for day in _solar_ingress_days_2026():
        # Etc/GMT-14 — это UTC+14: знак в именах Etc/ перевёрнут по POSIX
        first = _sun_sign_by_natal(day, time(0, 0), "Etc/GMT-14")
        last = _sun_sign_by_natal(day, time(23, 59), "Etc/GMT+12")
        sign = t.sun_sign_on(day)
        if sign is None:
            assert first != last, day
        else:
            assert first == last == sign, day


def test_sun_sign_on_refuses_about_two_days_a_month() -> None:
    """Отказов за год — около двух в месяц: больше — окно шире нужного."""
    year = [date(2026, 1, 1) + timedelta(days=i) for i in range(365)]
    refused = [d for d in year if t.sun_sign_on(d) is None]
    assert 12 <= len(refused) <= 36, len(refused)
