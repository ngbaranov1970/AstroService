"""Разбор свободного ввода данных рождения и поиск города с учётом падежей.

Люди пишут как угодно: «9,03,1987, время не помню, город Егорьевск»,
«родилась 9 марта 87 в 14.30 в Москве». Здесь — только текст → значения,
без БД и VK; решение «чего не хватает, что спросить» — в flows/natal.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time

from app.schemas.charts import MIN_BIRTH_YEAR
from app.services import charts as charts_service

# Сколько вариантов города показываем кнопками: inline VK — до 10 кнопок,
# и ещё нужна «Другой город».
PLACE_LIMIT = 5

_MONTHS = (
    ("янв", 1), ("фев", 2), ("мар", 3), ("апр", 4), ("ма", 5), ("июн", 6),
    ("июл", 7), ("авг", 8), ("сен", 9), ("окт", 10), ("ноя", 11), ("дек", 12),
)
# Хвост после года: «1987 г.», «1987 года», «1987г.р.» — иначе «г» уйдёт в город.
_YEAR_TAIL = r"(?:\s*(?:г(?:ода?)?\.?\s*(?:р\.?)?))?"
_DATE_NUM = re.compile(
    r"(?<!\d)(\d{1,2})\s*[.,/\\\-\s]\s*(\d{1,2})\s*[.,/\\\-\s]\s*(\d{4}|\d{2})(?!\d)"
    + _YEAR_TAIL
)
_DATE_ISO = re.compile(r"(?<!\d)(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})(?!\d)")
_DATE_WORD = re.compile(
    r"(?<!\d)(\d{1,2})\s*(?:-?го)?\s+([а-я]{3,9})\.?,?\s*(\d{4}|\d{2})(?!\d)"
    + _YEAR_TAIL
)
_DATE_COMPACT = re.compile(r"(?<!\d)(\d{2})(\d{2})(\d{4})(?!\d)")

_TIME_UNKNOWN = re.compile(
    r"(?:врем\w*\s*)?(?:не\s*(?:помню|знаю|известно|уверен\w*)|неизвестн\w*"
    r"|без\s+времени|хз|не\s+в\s+курсе)(?:\s*врем\w*)?"
)
_TIME_HM = re.compile(
    r"(?<![\d.])(\d{1,2})\s*(?::|\.|-|ч(?:ас(?:а|ов)?)?\.?)\s*(\d{2})(?!\d)\s*(?:мин\w*)?"
)
_TIME_HOUR_PART = re.compile(
    r"(?<!\d)(\d{1,2})\s*(?:ч(?:ас(?:а|ов)?)?\.?)?\s*(утра|дня|вечера|ночи)"
)
_TIME_HOUR = re.compile(r"(?<!\d)(\d{1,2})\s*(?:ч|час|часа|часов)\b\.?")
_TIME_BARE = re.compile(r"^\s*(?:в|около|примерно)?\s*(\d{1,2})\s*$")
_TIME_WORDS = {"полдень": time(12, 0), "полночь": time(0, 0)}

# Слова вокруг города, которые надо снять, чтобы осталось имя.
_CITY_MARKER = re.compile(
    r"\b(?:город[ае]?|гор\.|г\.|г|родил(?:ся|ась|ось|сь)|место\s+рождения)(?=\s|$|[.,])"
)
_NOISE = re.compile(
    r"\b(?:я|мой|моя|мое|дата|время|место|рождения|родил(?:ся|ась|ось|сь)|город[ае]?"
    r"|гор|г|в|во|из|с|со|около|примерно|где-то|года?|привет|здравствуйте|добрый\s+день"
    r"|утра|дня|вечера|ночи)\b\.?"
)
_SEGMENTS = re.compile(r"[,;\n]+")
_HAS_LETTERS = re.compile(r"[a-zа-я]{2,}")
# Падежные хвосты: «в Егорьевске», «из Казани», «Москвой». Длинные — первыми.
_CASE_ENDINGS = ("ами", "ями", "ом", "ем", "ой", "ей", "е", "и", "у", "ю", "а", "я")


@dataclass(frozen=True, slots=True)
class BirthGuess:
    """Что удалось вытащить из одного сообщения; None — поля в тексте нет."""

    birth_date: date | None = None
    birth_time: time | None = None
    time_unknown: bool = False
    city: str | None = None
    # Дата похожа на дату, но невозможна или вне 1900…сегодня: переспросить.
    bad_date: bool = False


def _norm(text: str) -> str:
    return text.lower().replace("ё", "е")


def _year(raw: str) -> int:
    year = int(raw)
    if len(raw) == 2:
        # «87» → 1987, «05» → 2005: всё, что не позже текущего года века, — 2000+.
        year += 2000 if year <= datetime.now(UTC).year % 100 else 1900
    return year


def _month_by_word(word: str) -> int | None:
    for stem, number in _MONTHS:
        if word.startswith(stem):
            # «ма» ловит «мая/май/мае», но и «марта» — его проверили раньше.
            return number
    return None


def _make_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _date_ok(value: date) -> bool:
    return MIN_BIRTH_YEAR <= value.year and value <= datetime.now(UTC).date()


def _cut(text: str, match: re.Match[str]) -> str:
    # Запятая на месте вырезанного держит границу сегментов для города.
    return text[: match.start()] + ", " + text[match.end() :]


def _take_date(text: str) -> tuple[date | None, bool, str]:
    """Найти дату; вернуть (дата, «похоже на дату, но невозможна», остаток)."""
    for pattern, order in (
        (_DATE_ISO, "ymd"),
        (_DATE_WORD, "dwy"),
        (_DATE_NUM, "dmy"),
        (_DATE_COMPACT, "dmy"),
    ):
        match = pattern.search(text)
        if match is None:
            continue
        a, b, c = match.group(1), match.group(2), match.group(3)
        if order == "ymd":
            value = _make_date(int(a), int(b), int(c))
        elif order == "dwy":
            month = _month_by_word(b)
            if month is None:
                continue
            value = _make_date(_year(c), month, int(a))
        else:
            value = _make_date(_year(c), int(b), int(a))
        rest = _cut(text, match)
        if value is None or not _date_ok(value):
            return None, True, rest
        return value, False, rest
    return None, False, text


def _take_time(text: str, *, expect_time: bool) -> tuple[time | None, bool, str]:
    """Найти время или «не помню»; вернуть (время, неизвестно, остаток)."""
    match = _TIME_UNKNOWN.search(text)
    if match is not None:
        return None, True, _cut(text, match)

    match = _TIME_HM.search(text)
    if match is not None:
        hour, minute = int(match.group(1)), int(match.group(2))
        if hour < 24 and minute < 60:
            return time(hour, minute), False, _cut(text, match)

    match = _TIME_HOUR_PART.search(text)
    if match is not None:
        hour, part = int(match.group(1)), match.group(2)
        if part in {"дня", "вечера"} and hour < 12:
            hour += 12
        elif part == "ночи" and hour == 12:
            hour = 0
        if hour < 24:
            return time(hour, 0), False, _cut(text, match)

    match = _TIME_HOUR.search(text)
    if match is not None and int(match.group(1)) < 24:
        return time(int(match.group(1)), 0), False, _cut(text, match)

    for word, value in _TIME_WORDS.items():
        if word in text:
            return value, False, text.replace(word, ", ")

    # Голое «14» — время, только если его и спрашивали; иначе это мусор.
    if expect_time:
        match = _TIME_BARE.match(text)
        if match is not None and int(match.group(1)) < 24:
            return time(int(match.group(1)), 0), False, ""
    return None, False, text


def _take_city(text: str, *, force: bool) -> str | None:
    """Кандидат в город из остатка; без маркера и без force — None."""
    segments = [s.strip(" .-—:") for s in _SEGMENTS.split(text)]
    marked = [s for s in segments if _CITY_MARKER.search(s)]
    # Сегмент с маркером может оказаться пустым («родилась, …, в Москве»):
    # тогда при force смотрим и остальные.
    pool = marked + [s for s in segments if s not in marked] if force else marked
    for segment in pool:
        cleaned = _NOISE.sub(" ", segment)
        cleaned = re.sub(r"[^\w\s\-]", " ", cleaned)
        cleaned = re.sub(r"\d+", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -")
        if _HAS_LETTERS.search(cleaned):
            return cleaned
    return None


def parse_birth(text: str, *, expect: str | None = None) -> BirthGuess:
    """Вытащить из сообщения всё, что похоже на дату, время и город.

    expect — что спрашивали последним ("date" / "time" / "city"): голое «14»
    считается часом только при "time", а текст без маркера «город» — городом
    только при "city" или если рядом нашлась дата/время (сообщение «всё сразу»).
    """
    work = _norm(text)
    birth_date, bad_date, work = _take_date(work)
    birth_time, time_unknown, work = _take_time(work, expect_time=expect == "time")
    # «не знаю» само по себе не делает остаток городом: «ну не знаю даже».
    structured = birth_date is not None or birth_time is not None
    city = _take_city(work, force=expect == "city" or structured)
    return BirthGuess(
        birth_date=birth_date,
        birth_time=birth_time,
        time_unknown=time_unknown,
        city=city,
        bad_date=bad_date,
    )


def _stems(word: str) -> list[str]:
    """Слово без падежных хвостов, от короткого среза к длинному не короче 3 букв."""
    out = []
    for ending in _CASE_ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= 3:
            out.append(word[: -len(ending)])
    return out


_TOKENS = re.compile(r"[\s\-]+")


@dataclass(frozen=True, slots=True)
class PlaceSearch:
    """Найденные города; sure — совпадение однозначное, переспрашивать не надо."""

    places: list[charts_service.Place]
    sure: bool


def _name(place: charts_service.Place) -> str:
    return _norm(place.label.rsplit(",", 1)[0])


def _country(place: charts_service.Place) -> str:
    return place.label.rsplit(",", 1)[-1].strip()


def _word_fits(name_word: str, word: str) -> bool:
    if name_word == word:
        return True
    # Падеж: «москве» ↔ «москва», «нижнем» ↔ «нижний» — основа + хвост до 3 букв.
    return any(
        name_word.startswith(stem) and len(name_word) - len(stem) <= 3
        for stem in _stems(word)
    )


def _fits(place: charts_service.Place, tokens: list[str]) -> bool:
    name_tokens = _TOKENS.split(_name(place))
    return len(name_tokens) == len(tokens) and all(
        _word_fits(n, w) for n, w in zip(name_tokens, tokens, strict=True)
    )


def find_places(city: str) -> PlaceSearch:
    """Города по кандидату из parse_birth; пустой places — не нашли.

    Справочник ищет по началу имени, поэтому «Егорьевске» находится только
    по основе. Хвост вроде «Московской области» отбрасываем, сверяя имя
    пословно по первым словам запроса.
    """
    target = _norm(city).strip()
    tokens = [t for t in _TOKENS.split(target) if t]
    if not tokens:
        return PlaceSearch([], sure=False)

    candidates: list[charts_service.Place] = []
    for query in [target, tokens[0], *_stems(tokens[0])]:
        # 50, а не 5: по короткой основе «нижн» нужный город может быть не первым.
        candidates = charts_service.search_places(query, limit=50)
        if candidates:
            break
    if not candidates:
        return PlaceSearch([], sure=False)

    for size in range(len(tokens), 0, -1):
        matched = [p for p in candidates if _fits(p, tokens[:size])]
        if matched:
            russian = [p for p in matched if _country(p) == "RU"]
            # Аудитория русскоязычная: единственная «Москва, RU» важнее «Москвы, US».
            sure = len(matched) == 1 or len(russian) == 1
            ordered = russian + [p for p in matched if p not in russian]
            return PlaceSearch(ordered[:PLACE_LIMIT], sure=sure)
    return PlaceSearch(candidates[:PLACE_LIMIT], sure=len(candidates) == 1)
