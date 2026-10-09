"""Проверка фактов VIP-черновика: ловит ошибки и не врёт на верных фразах.

Карта искусственная — проверяется разбор текста, а не астрология. Каждый
«ложный» случай здесь — реальная фраза из приёмки, на которой эвристика
однажды ошибалась.
"""

from __future__ import annotations

from app.services.vip_check import check


def _p(name, sign, house, deg):
    return {"name_ru": name, "sign_ru": sign, "house": house, "abs_deg": deg}


def _a(p1, kind, p2, orb=1.0):
    return {"p1_ru": p1, "type_ru": kind, "p2_ru": p2, "orb": orb}


NATAL = {
    "planets": [
        _p("Солнце", "Рак", 10, 112.0),
        _p("Марс", "Овен", 7, 0.2),
        _p("Венера", "Близнецы", 10, 75.0),
        _p("Сатурн", "Стрелец", 4, 268.0),
        _p("Уран", "Стрелец", 4, 269.0),
        _p("Нептун", "Козерог", 5, 278.0),
        _p("Юпитер", "Близнецы", 9, 62.0),
        _p("Хирон", "Рак", 10, 100.0),
    ],
    "angles": {
        "asc": {"name_ru": "Асцендент", "sign_ru": "Дева", "abs_deg": 160.0, "house": None},
        "mc": {"name_ru": "Середина неба", "sign_ru": "Близнецы", "abs_deg": 66.0, "house": None},
    },
    "houses": [{"num": n, "abs_deg": (160.0 + 30 * (n - 1)) % 360} for n in range(1, 13)],
    "aspects": [
        _a("Венера", "секстиль", "Нептун"),
        _a("Венера", "трин", "Сатурн"),
        _a("Сатурн", "соединение", "Уран"),
        _a("Нептун", "оппозиция", "Сатурн"),
        _a("Марс", "квадрат", "Сатурн"),
        _a("Марс", "секстиль", "Середина неба"),
        _a("Юпитер", "соединение", "Середина неба"),
    ],
}


def _kinds(text, **kw):
    return [(f.kind, f.message) for f in check(text, NATAL, **kw)]


def test_wrong_house_found():
    [(kind, msg)] = _kinds("Венера во втором доме делает тебя щедрой.")
    assert kind == "house" and "в 10 доме" in msg


def test_house_checked_per_planet_in_group():
    [(_, msg)] = _kinds("Сатурн и Нептун в четвёртом доме.")
    assert msg.startswith("Нептун")


def test_house_with_sign_between():
    assert _kinds("Марс в Овне в седьмом доме.") == []


def test_ruler_is_not_placement():
    assert _kinds("Управитель второго дома Венера стоит в десятом доме.") == []


def test_wrong_sign_found():
    [(kind, _)] = _kinds("Сатурн, Уран и Нептун в Стрельце.")
    assert kind == "sign"


def test_cusp_far_found_near_ok():
    [(kind, msg)] = _kinds("Марс в Овне на куспиде седьмого дома.")
    assert kind == "cusp" and "от куспида 7 дома" in msg
    # Куспид 5 дома — 280°, Нептун в 278°: ровно CUSP_ORB, ещё «на куспиде».
    assert _kinds("Нептун на куспиде пятого дома.") == []


def test_aspect_chain_keeps_subject():
    """«к Нептуну и в трине к Сатурну» — трин у Венеры, а не у Нептуна."""
    assert _kinds("Венера в секстиле к Нептуну и в трине к Сатурну — смесь редкая.") == []


def test_aspect_wrong_type_and_missing():
    found = _kinds("Венера в квадрате к Сатурну. Марс в трине к Юпитеру.")
    assert [k for k, _ in found] == ["aspect", "aspect"]
    assert "трин" in found[0][1] and "нет" in found[1][1]


def test_aspect_pair_after_word():
    """«соединение Юпитера с МС» — пара после слова, Марс из начала ни при чём."""
    assert _kinds("Секстиль Марса к Середине неба и соединение Юпитера с МС дают амбиции.") == []


def test_object_without_subject_skipped():
    """«квадратов с Сатурном, Ураном»: чьих — не понять, место пропускается."""
    assert _kinds("И целая серия его квадратов с Сатурном, Ураном и Хироном.") == []


def test_no_time_flags_houses_and_angles():
    found = _kinds("Марс в седьмом доме. Асцендент в Деве.", time_known=False)
    assert [k for k, _ in found] == ["no_time", "no_time"]


def test_lowercase_metaphor_ignored():
    assert _kinds("Ты тянешься к солнцу в Раке.") == []
