"""Календарь астрособытий: лунации, затмения, станции, ингрессы, аспекты.

Ядро не знает про VK, LLM и базу: вход — интервал дат, выход — список
событий и «небо дня» в dict'ах. Отсюда контент сообщества берёт инфоповоды
(docs/astro-context.md, «Контур сообщества VK»).

Все моменты возвращаются в московской зоне: посты и рассылка адресованы
российской аудитории, а расхождение на сутки — типовая ошибка чужих пабликов.

Константы знаков, планет и аспектов импортируются из natal.py. Второй набор
неизбежно разошёлся бы с первым, и «Меркурий» в посте назывался бы иначе,
чем в разборе карты.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import NamedTuple
from zoneinfo import ZoneInfo

import swisseph as swe

from astro_core.natal import ASPECTS_RU, PLANETS_RU, SIGNS_RU

MSK = ZoneInfo("Europe/Moscow")

# Знаки в порядке зодиака: swisseph отдаёт долготу, номер знака считаем сами.
# Коды те же, что у kerykeion в natal.py, — иначе SIGNS_RU не подойдёт.
_SIGN_CODES = [
    "Ari", "Tau", "Gem", "Can", "Leo", "Vir",
    "Lib", "Sco", "Sag", "Cap", "Aqu", "Pis",
]

# Падежи держим таблицей, а не морфологическим анализатором (pymorphy3 и
# подобные). Слов ровно 22 и новых не будет, а на знаках анализатор всё
# равно ошибается: «Рыбы» он разбирает как единственное число слова «рыба»
# и выдаёт «в рыбе» (проверено 22.08.2026). Предлог «в/во» он тоже не
# выбирает, поэтому «во Льве» пришлось бы дописывать руками.


class _SignForms(NamedTuple):
    """Формы знака. Именительный лежит в natal.SIGNS_RU, здесь его нет."""

    gen: str  # «входит в знак Овна»
    loc: str  # «в Овне» — вместе с предлогом: у Льва он «во»


# Родительный для оборота «входит в знак Девы»: винительный короче, но
# требует различать одушевлённость («в Овен», однако «во Льва»), а этот
# оборот одинаково верен для всех двенадцати.
_SIGN_FORMS = {
    "Ari": _SignForms("Овна", "в Овне"),
    "Tau": _SignForms("Тельца", "в Тельце"),
    "Gem": _SignForms("Близнецов", "в Близнецах"),
    "Can": _SignForms("Рака", "в Раке"),
    "Leo": _SignForms("Льва", "во Льве"),
    "Vir": _SignForms("Девы", "в Деве"),
    "Lib": _SignForms("Весов", "в Весах"),
    "Sco": _SignForms("Скорпиона", "в Скорпионе"),
    "Sag": _SignForms("Стрельца", "в Стрельце"),
    "Cap": _SignForms("Козерога", "в Козероге"),
    "Aqu": _SignForms("Водолея", "в Водолее"),
    "Pis": _SignForms("Рыб", "в Рыбах"),
}


class _PlanetForms(NamedTuple):
    """Формы планеты. Именительный — в natal.PLANETS_RU."""

    dat: str  # «в квадрате к Сатурну»
    ins: str  # «в соединении с Сатурном»
    ending: str  # окончание причастия: «становится ретроградным / ретроградной»


_PLANET_FORMS = {
    "Sun": _PlanetForms("Солнцу", "Солнцем", "ым"),
    "Moon": _PlanetForms("Луне", "Луной", "ой"),
    "Mercury": _PlanetForms("Меркурию", "Меркурием", "ым"),
    "Venus": _PlanetForms("Венере", "Венерой", "ой"),
    "Mars": _PlanetForms("Марсу", "Марсом", "ым"),
    "Jupiter": _PlanetForms("Юпитеру", "Юпитером", "ым"),
    "Saturn": _PlanetForms("Сатурну", "Сатурном", "ым"),
    "Uranus": _PlanetForms("Урану", "Ураном", "ым"),
    "Neptune": _PlanetForms("Нептуну", "Нептуном", "ым"),
    "Pluto": _PlanetForms("Плутону", "Плутоном", "ым"),
}

# Соединение управляет творительным с предлогом «с», остальные аспекты —
# дательным с «к»: «в соединении с Меркурием», но «в квадрате к Урану».
_ASPECT_IN = {
    "conjunction": "в соединении",
    "sextile": "в секстиле",
    "square": "в квадрате",
    "trine": "в трине",
    "opposition": "в оппозиции",
}

# Тела, которые участвуют в транзитах. Узлы и Хирон из натальной карты сюда
# не берём: в ленте сообщества они требуют объяснения на абзац.
_BODIES = {
    "Sun": swe.SUN,
    "Moon": swe.MOON,
    "Mercury": swe.MERCURY,
    "Venus": swe.VENUS,
    "Mars": swe.MARS,
    "Jupiter": swe.JUPITER,
    "Saturn": swe.SATURN,
    "Uranus": swe.URANUS,
    "Neptune": swe.NEPTUNE,
    "Pluto": swe.PLUTO,
}

# Ретроградными бывают все, кроме светил.
_RETRO_CAPABLE = [n for n in _BODIES if n not in ("Sun", "Moon")]

# Углы аспектов. В natal.ASPECTS_RU только названия, углов там нет — это
# дополнение к нему, а не копия: ключи обязаны совпадать.
ASPECT_ANGLES = {
    "conjunction": 0.0,
    "sextile": 60.0,
    "square": 90.0,
    "trine": 120.0,
    "opposition": 180.0,
}

# Орбы для «небо дня»: транзитные, а не натальные — в карте орб задаёт
# kerykeion, здесь аспект живёт часами и широкий орб размывает картину дня.
_DAY_ORBS = {
    "conjunction": 6.0,
    "opposition": 6.0,
    "trine": 5.0,
    "square": 5.0,
    "sextile": 3.0,
}

_PHASES_RU = [
    (0, "новолуние"),
    (90, "первая четверть"),
    (180, "полнолуние"),
    (270, "последняя четверть"),
]

# Названия промежутков между четвертями: (угол начала промежутка, имя).
# Без них половина месяца подписывалась бы чужой четвертью — пост сообщал бы
# «сегодня первая четверть» через два дня после неё.
_PHASES_BETWEEN_RU = [
    (0, "растущий серп"),
    (90, "прибывающая Луна"),
    (180, "убывающая Луна"),
    (270, "убывающий серп"),
]

# Точность поиска момента: 1e-6 суток — это 0.09 секунды, заведомо мельче
# любой ошибки самих эфемерид.
_TOL_DAYS = 1e-6

# Считаем по Moshier — рядам, зашитым в саму библиотеку; файлы эфемерид ему
# не нужны, и swe.set_ephe_path на результат не влияет (проверено 22.08.2026:
# значения совпадают до девятого знака). Планетных .se1 в проекте всё равно
# нет — пакет kerykeion возит только астероидные, — так что FLG_SWIEPH молча
# откатился бы на тот же Moshier. Просим его явно, чтобы фолбэк не был
# молчаливым. Точность 0.1–0.3" против порога золотых тестов в 1'.
_CALC_FLAG = swe.FLG_MOSEPH | swe.FLG_SPEED


def _to_jd(moment: datetime) -> float:
    """Julian Day (UT) из datetime; naive считается московским."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=MSK)
    utc = moment.astimezone(UTC)
    hours = utc.hour + utc.minute / 60 + (utc.second + utc.microsecond / 1e6) / 3600
    return swe.julday(utc.year, utc.month, utc.day, hours, swe.GREG_CAL)


def _to_msk(jd: float) -> datetime:
    """Момент по Julian Day в московской зоне, округлённый до секунды."""
    year, month, day, hours = swe.revjul(jd, swe.GREG_CAL)
    base = datetime(year, month, day, tzinfo=UTC)
    moment = base + timedelta(hours=hours)
    # Округляем до секунды: доли секунды в эфемеридах Moshier не значимы, а в
    # заголовке поста выглядят ложной точностью.
    moment += timedelta(microseconds=500_000)
    return moment.replace(microsecond=0).astimezone(MSK)


def _lon_speed(jd: float, body: str) -> tuple[float, float]:
    """Эклиптическая долгота и суточная скорость тела на момент jd."""
    pos, _ = swe.calc_ut(jd, _BODIES[body], _CALC_FLAG)
    return pos[0], pos[3]


def _sign_index(lon: float) -> int:
    return int(lon % 360.0 // 30)


def _norm180(angle: float) -> float:
    """Угол в диапазон (-180, 180] — так у функции есть знак и есть ноль."""
    return (angle + 180.0) % 360.0 - 180.0


def _refine(func, lo: float, hi: float) -> float:
    """Уточняет момент нуля func делением отрезка пополам.

    Вызывающий гарантирует, что на концах знаки разные. Полсотни итераций
    здесь не нужны: отрезок в сутки ужимается до 0.09 с за 20 шагов.
    """
    f_lo = func(lo)
    while hi - lo > _TOL_DAYS:
        mid = (lo + hi) / 2
        f_mid = func(mid)
        if (f_lo <= 0) == (f_mid <= 0):
            lo, f_lo = mid, f_mid
        else:
            hi = mid
    return (lo + hi) / 2


def _scan(func, jd_start: float, jd_end: float, step: float, *, wrapped: bool = False):
    """Идёт по интервалу шагом step и отдаёт моменты смены знака func.

    wrapped=True — для угловых функций: у них есть разрыв на ±180, который
    выглядит как смена знака. Скачок больше 90 за шаг считаем разрывом, а не
    корнем; шаг обязан быть мельче, чем движение тел за это время.
    """
    jd = jd_start
    prev = func(jd)
    while jd < jd_end:
        nxt = min(jd + step, jd_end)
        cur = func(nxt)
        if (prev <= 0) != (cur <= 0) and not (wrapped and abs(cur - prev) > 90.0):
            yield _refine(func, jd, nxt)
        jd, prev = nxt, cur


def _event(kind: str, jd: float, title: str, **extra) -> dict:
    """Единая форма события: у всех видов одинаковые ключи плюс свои."""
    return {"kind": kind, "moment": _to_msk(jd), "title_ru": title, **extra}


def _sign_fields(lon: float) -> dict:
    code = _SIGN_CODES[_sign_index(lon)]
    forms = _SIGN_FORMS[code]
    return {
        "sign": code,
        "sign_ru": SIGNS_RU[code],
        # Готовые падежи рядом с именительным: без них текст пишет их сам и
        # ошибается («в Раку» в первом же прогоне daily_sky 05.09.2026).
        "sign_gen": forms.gen,
        "sign_loc": forms.loc,
        "deg_in_sign": round(lon % 30.0, 2),
    }


def lunations(start: date, end: date) -> list[dict]:
    """Новолуния и полнолуния интервала.

    Ищет, где разность долгот Луны и Солнца проходит через 0 и через 180.
    """
    jd_start, jd_end = _to_jd(_day_start(start)), _to_jd(_day_start(end + timedelta(days=1)))
    out: list[dict] = []
    for target, kind, name in ((0.0, "new_moon", "Новолуние"), (180.0, "full_moon", "Полнолуние")):
        def diff(jd: float, _t=target) -> float:
            return _norm180(_lon_speed(jd, "Moon")[0] - _lon_speed(jd, "Sun")[0] - _t)

        # Шаг в полсуток: Луна уходит от Солнца на 12° в сутки, промахнуться
        # мимо корня на таком шаге она не успевает.
        for jd in _scan(diff, jd_start, jd_end, 0.5, wrapped=True):
            lon = _lon_speed(jd, "Moon")[0]
            fields = _sign_fields(lon)
            out.append(
                _event(kind, jd, f"{name} {_SIGN_FORMS[fields['sign']].loc}", body="Moon",
                       body_ru=PLANETS_RU["Moon"], **fields)
            )
    return out


def eclipses(start: date, end: date) -> list[dict]:
    """Солнечные и лунные затмения интервала (по всему земному шару)."""
    jd_start, jd_end = _to_jd(_day_start(start)), _to_jd(_day_start(end + timedelta(days=1)))
    flag = swe.FLG_MOSEPH
    out: list[dict] = []

    for finder, kind, body, name in (
        (swe.sol_eclipse_when_glob, "solar_eclipse", "Sun", "Солнечное затмение"),
        (swe.lun_eclipse_when, "lunar_eclipse", "Moon", "Лунное затмение"),
    ):
        jd = jd_start
        while jd < jd_end:
            retflag, tret = finder(jd, flag, 0, False)
            peak = tret[0]
            if peak >= jd_end:
                break
            kindness = _eclipse_kind(retflag)
            lon = _lon_speed(peak, body)[0]
            fields = _sign_fields(lon)
            out.append(
                _event(kind, peak, f"{name} ({kindness}) {_SIGN_FORMS[fields['sign']].loc}",
                       body=body, body_ru=PLANETS_RU[body], eclipse_kind=kindness, **fields)
            )
            # Следующее затмение ищем от момента после текущего: та же дата
            # вернула бы то же событие бесконечно.
            jd = peak + 1.0
    return out


def _eclipse_kind(retflag: int) -> str:
    if retflag & swe.ECL_TOTAL:
        return "полное"
    if retflag & swe.ECL_ANNULAR:
        return "кольцеобразное"
    if retflag & swe.ECL_PENUMBRAL:
        return "полутеневое"
    return "частное"


def stations(start: date, end: date) -> list[dict]:
    """Моменты, когда планета разворачивается: скорость проходит через ноль."""
    jd_start, jd_end = _to_jd(_day_start(start)), _to_jd(_day_start(end + timedelta(days=1)))
    out: list[dict] = []
    for body in _RETRO_CAPABLE:
        def speed(jd: float, _b=body) -> float:
            return _lon_speed(jd, _b)[1]

        for jd in _scan(speed, jd_start, jd_end, 1.0):
            # Знак скорости ПОСЛЕ момента разворота и говорит, куда пошли:
            # в самой точке она равна нулю и направление по ней не читается.
            forward = speed(jd + 0.5) > 0
            kind = "station_direct" if forward else "station_retrograde"
            ending = _PLANET_FORMS[body].ending
            verb = f"становится {'директн' if forward else 'ретроградн'}{ending}"
            fields = _sign_fields(_lon_speed(jd, body)[0])
            out.append(
                _event(kind, jd, f"{PLANETS_RU[body]} {verb} {_SIGN_FORMS[fields['sign']].loc}",
                       body=body, body_ru=PLANETS_RU[body], **fields)
            )
    return out


def ingresses(start: date, end: date) -> list[dict]:
    """Входы тел в новый знак.

    Луна исключена намеренно: она меняет знак каждые два с половиной дня, и
    в ленте это шум. Её положение отдаёт day_sky как состояние дня.
    """
    jd_start, jd_end = _to_jd(_day_start(start)), _to_jd(_day_start(end + timedelta(days=1)))
    out: list[dict] = []
    for body in (b for b in _BODIES if b != "Moon"):
        for jd in _scan_sign_change(body, jd_start, jd_end, 1.0):
            # Знак берём чуть позже момента перехода: ровно на границе долгота
            # равна 0°00'00" нового знака, и округление может отдать предыдущий.
            fields = _sign_fields(_lon_speed(jd + 0.01, body)[0])
            out.append(
                _event("ingress", jd,
                       f"{PLANETS_RU[body]} входит в знак {_SIGN_FORMS[fields['sign']].gen}",
                       body=body, body_ru=PLANETS_RU[body], **fields)
            )
    return out


def sun_sign_on(day: date) -> str | None:
    """Знак Солнца по одной дате рождения («Лев») или None в день смены знака.

    None значит: где-то на Земле эта дата захватывает переход Солнца в
    следующий знак, и без времени и места знак не определить. Так бывает
    около двух дней в месяц (docs/astro-context.md, «Знак для рассылки —
    без карты»).
    """
    # Одна календарная дата длится 50 часов: начинается в UTC+14 и кончается
    # в UTC−12. Солнце назад не ходит, поэтому хватает двух концов окна.
    start = datetime(day.year, day.month, day.day, tzinfo=UTC) - timedelta(hours=14)
    end = start + timedelta(hours=50)
    first = _sign_index(_lon_speed(_to_jd(start), "Sun")[0])
    last = _sign_index(_lon_speed(_to_jd(end), "Sun")[0])
    if first != last:
        return None
    return SIGNS_RU[_SIGN_CODES[first]]


def _scan_sign_change(body: str, jd_start: float, jd_end: float, step: float):
    """Отдаёт моменты, когда тело переходит границу знака.

    Границу нельзя искать как ноль непрерывной функции: остаток долготы по
    30° в этой точке не обращается в ноль, а разрывается (30° → 0°). Поэтому
    делим отрезок по предикату «знак ещё прежний», а не по смене знака
    функции. Шаг обязан быть мельче, чем 30° хода тела.
    """
    jd = jd_start
    idx = _sign_index(_lon_speed(jd, body)[0])
    while jd < jd_end:
        nxt = min(jd + step, jd_end)
        idx_next = _sign_index(_lon_speed(nxt, body)[0])
        if idx_next != idx:
            lo, hi = jd, nxt
            while hi - lo > _TOL_DAYS:
                mid = (lo + hi) / 2
                if _sign_index(_lon_speed(mid, body)[0]) == idx:
                    lo = mid
                else:
                    hi = mid
            yield (lo + hi) / 2
        jd, idx = nxt, idx_next


def exact_aspects(start: date, end: date, *, include_moon: bool = False) -> list[dict]:
    """Моменты, когда аспект между двумя телами становится точным.

    include_moon=False по умолчанию: Луна замыкает аспект с каждым телом
    дважды в месяц, и в календаре событий это перекрывает всё остальное.
    """
    jd_start, jd_end = _to_jd(_day_start(start)), _to_jd(_day_start(end + timedelta(days=1)))
    bodies = [b for b in _BODIES if include_moon or b != "Moon"]
    # Шаг мельче для Луны: она проходит 13° в сутки, и точный аспект живёт
    # считанные часы.
    step = 0.25 if include_moon else 1.0
    out: list[dict] = []

    for i, first in enumerate(bodies):
        for second in bodies[i + 1:]:
            for aspect, angle in ASPECT_ANGLES.items():
                for target in _aspect_targets(angle):
                    def gap(jd: float, _a=first, _b=second, _t=target) -> float:
                        return _norm180(_lon_speed(jd, _a)[0] - _lon_speed(jd, _b)[0] - _t)

                    for jd in _scan(gap, jd_start, jd_end, step, wrapped=True):
                        out.append(_aspect_event("aspect", jd, first, second, aspect))
    return out


def _aspect_targets(angle: float) -> tuple[float, ...]:
    """Смещения, на которых аспект точен: у 0° и 180° одно, у остальных два.

    Разность долгот проходит и +90, и −90, и это два разных момента — у Луны
    с Солнцем первая и последняя четверть. Ищем оба: пока искали только
    +angle, находилась ровно одна половина цикла, а какая именно — решал
    порядок тел в _BODIES. За 2026 год так терялось 56 аспектов из 187.
    """
    return (angle,) if angle in (0.0, 180.0) else (angle, -angle)


def aspect_phrase(first: str, second: str, aspect: str) -> str:
    """«Юпитер в трине к Сатурну» — аспект фразой с падежами.

    Нужна не только событиям: действующие аспекты в day_sky["aspects"] — не
    события, готового title_ru у них нет, а собирать фразу из трёх слов в
    именительном падеже текст поста не умеет («Юпитер в трине с Сатурном» в
    прогоне 05.09.2026).
    """
    # Соединение просит творительный с «с», прочие аспекты — дательный с «к».
    if aspect == "conjunction":
        tail = f"с {_PLANET_FORMS[second].ins}"
    else:
        tail = f"к {_PLANET_FORMS[second].dat}"
    return f"{PLANETS_RU[first]} {_ASPECT_IN[aspect]} {tail}"


def _aspect_event(kind: str, jd: float, first: str, second: str, aspect: str) -> dict:
    """Событие точного аспекта: заголовок с нужным падежом и поля обоих тел.

    Вынесено из exact_aspects, потому что то же самое собирает moon_events:
    два места, где падежи набираются отдельно, однажды разойдутся.
    """
    return _event(
        kind, jd,
        aspect_phrase(first, second, aspect),
        body=first, body_ru=PLANETS_RU[first],
        body2=second, body2_ru=PLANETS_RU[second],
        aspect=aspect, aspect_ru=ASPECTS_RU[aspect],
        **_sign_fields(_lon_speed(jd, first)[0]),
    )


def moon_events(day: date) -> list[dict]:
    """Ингресс Луны и её точные аспекты за сутки — данные для поста дня.

    Отдельно от events_between: там Луна исключена намеренно (её ингресс
    каждые два с половиной дня и по аспекту в несколько часов забивают
    недельный календарь), а посту про небо суток нужны именно они.
    Виды событий свои — moon_ingress и moon_aspect, чтобы отбор постов по
    весу в services/content.py не спутал их с планетными.
    """
    jd_start = _to_jd(_day_start(day))
    jd_end = _to_jd(_day_start(day + timedelta(days=1)))
    out: list[dict] = []

    # Шаг полсуток: 30° знака Луна проходит за 2.3 суток, границу не перескочит.
    for jd in _scan_sign_change("Moon", jd_start, jd_end, 0.5):
        fields = _sign_fields(_lon_speed(jd + 0.01, "Moon")[0])
        # Знак, из которого Луна вышла: тексту нужно «до этого она в
        # Близнецах». Считаем по долготе за четверть часа до момента, а не
        # берём соседний слева по зодиаку — счёт по списку это догадка, а
        # эфемериды знают ответ.
        previous = _sign_fields(_lon_speed(jd - 0.01, "Moon")[0])
        out.append(
            _event("moon_ingress", jd,
                   f"Луна входит в знак {_SIGN_FORMS[fields['sign']].gen}",
                   body="Moon", body_ru=PLANETS_RU["Moon"],
                   from_sign=previous["sign"],
                   from_sign_ru=previous["sign_ru"],
                   from_sign_loc=previous["sign_loc"],
                   **fields)
        )

    for second in (b for b in _BODIES if b != "Moon"):
        for aspect, angle in ASPECT_ANGLES.items():
            for target in _aspect_targets(angle):
                def gap(jd: float, _b=second, _t=target) -> float:
                    return _norm180(_lon_speed(jd, "Moon")[0] - _lon_speed(jd, _b)[0] - _t)

                # Шаг шесть часов, как в exact_aspects с Луной: за них она
                # проходит 3.3°, и разрыв угла на ±180 не выглядит корнем.
                for jd in _scan(gap, jd_start, jd_end, 0.25, wrapped=True):
                    out.append(_aspect_event("moon_aspect", jd, "Moon", second, aspect))

    return sorted(out, key=lambda e: e["moment"])


def next_lunation(day: date) -> dict | None:
    """Ближайшее новолуние или полнолуние в этот день либо после него.

    К полям события добавляет days_until — целое число суток до него по
    календарю МСК (0 = сегодня). None не возвращается на практике: горизонт
    поиска шире синодического месяца.
    """
    # 40 суток: между лунациями 14.8 в среднем, между одноимёнными 29.5.
    ahead = sorted(lunations(day, day + timedelta(days=40)), key=lambda e: e["moment"])
    for event in ahead:
        if event["moment"].date() >= day:
            return {**event, "days_until": (event["moment"].date() - day).days}
    return None


def events_between(start: date, end: date) -> list[dict]:
    """Все события интервала (включая обе даты), по времени.

    Аспекты — только между планетами без Луны: календарь недели должен
    читаться как список поводов, а не как эфемеридная таблица.
    """
    events = (
        lunations(start, end)
        + eclipses(start, end)
        + stations(start, end)
        + ingresses(start, end)
        + exact_aspects(start, end)
    )
    return sorted(events, key=lambda e: e["moment"])


def day_sky(day: date) -> dict:
    """«Небо дня» для гороскопа: положения тел, фаза Луны, аспекты, события.

    Положения берутся на полдень МСК — середина суток, к которой относится
    текст поста. Аспекты здесь действующие (в орбе), а не только точные:
    гороскоп описывает настроение дня, а не мгновение.
    """
    noon = _to_jd(datetime(day.year, day.month, day.day, 12, 0, tzinfo=MSK))

    positions = []
    for body in _BODIES:
        lon, speed = _lon_speed(noon, body)
        positions.append(
            {
                "name": body,
                "name_ru": PLANETS_RU[body],
                "retrograde": speed < 0,
                **_sign_fields(lon),
            }
        )

    active = []
    names = list(_BODIES)
    for i, first in enumerate(names):
        for second in names[i + 1:]:
            gap = abs(_norm180(_lon_speed(noon, first)[0] - _lon_speed(noon, second)[0]))
            for aspect, angle in ASPECT_ANGLES.items():
                orb = abs(gap - angle)
                if orb <= _DAY_ORBS[aspect]:
                    delta = _orb_speed(noon, first, second, angle, orb)
                    active.append(
                        {
                            "body": first, "body_ru": PLANETS_RU[first],
                            "body2": second, "body2_ru": PLANETS_RU[second],
                            "aspect": aspect, "aspect_ru": ASPECTS_RU[aspect],
                            "orb": round(orb, 2),
                            # Сходящийся аспект нарастает, расходящийся отпускает —
                            # в тексте это разные советы на один и тот же аспект.
                            "applying": delta < 0,
                            "orb_delta_day": round(delta, 2),
                        }
                    )

    return {
        "date": day.isoformat(),
        "positions": positions,
        "moon": _moon_phase(noon),
        "aspects": sorted(active, key=lambda a: a["orb"]),
        "events": [e for e in events_between(day, day)],
        # Луна отдельными ключами, а не внутри events: в events смотрит промпт
        # 12-знакового гороскопа, и добавка туда переписала бы тексты рассылки.
        "moon_events": moon_events(day),
        "next_lunation": next_lunation(day),
    }


def _orb_speed(jd: float, first: str, second: str, angle: float, orb: float) -> float:
    """Насколько градусов изменится орб аспекта за сутки: минус — сходится.

    Считает мгновенную скорость по короткому шагу, а не разницу орбов через
    сутки: аспект, точный внутри этих суток, по концам отрезка выглядел бы
    расходящимся при том, что сейчас он ещё сходится.
    """
    # Полчаса: Луна за них проходит 0.27° — сдвиг заметен на фоне округления
    # до сотых, а знак производной ещё не успевает смениться.
    step = 1 / 48
    gap_next = abs(_norm180(_lon_speed(jd + step, first)[0] - _lon_speed(jd + step, second)[0]))
    return (abs(gap_next - angle) - orb) / step


def _moon_phase(jd: float) -> dict:
    """Фаза Луны: угол от Солнца и название фазы — четверть или промежуток."""
    moon_lon, moon_speed = _lon_speed(jd, "Moon")
    sun_lon, sun_speed = _lon_speed(jd, "Sun")
    angle = (moon_lon - sun_lon) % 360.0
    return {
        "phase_angle": round(angle, 2),
        # Орб — половина суточного хода Луны от Солнца, то есть «точный момент
        # четверти попадает в эти сутки». Фиксированное число тут не годится:
        # ход гуляет от 10.8° до 14.4° в сутки, и в быстрый месяц четверть
        # проваливалась бы между двумя полуднями, не подписав ни одного дня.
        "phase_ru": _phase_name(angle, (moon_speed - sun_speed) / 2),
        "growing": angle < 180.0,
        # Долю освещённого диска берём у swisseph, а не считаем от угла
        # Луна–Солнце: угол этот геоцентрический, а освещённость задаёт
        # солнечный фазовый угол, и у Луны они расходятся на градусы.
        "illumination": round(swe.pheno_ut(jd, swe.MOON, swe.FLG_MOSEPH)[1] * 100),
        **_sign_fields(moon_lon),
    }


def _phase_name(angle: float, orb: float) -> str:
    """Имя фазы по углу Луна–Солнце: четверть в орбе, иначе имя промежутка."""
    for target, name in _PHASES_RU:
        if abs(_norm180(angle - target)) <= orb:
            return name
    return _PHASES_BETWEEN_RU[int(angle // 90)][1]


def _day_start(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=MSK)
