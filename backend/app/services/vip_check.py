"""Проверка фактов VIP-черновика по расчёту карты: подсветка на вычитку.

Ловит ошибки, которые промпт не лечит (docs/astro-prompts-v2.md, раздел 9):
планета не в том знаке или доме, «на куспиде» без оснований, аспект не того
типа или которого нет в расчёте. Без времени рождения помечает любое
упоминание домов и углов.

Разбор русского текста — эвристика по соседству слов: проверяются только
простые конструкции («Сатурн и Уран в четвёртом доме», «Марс квадрат
Сатурну»). Где связь неясна, место пропускается: ложная подсветка хуже
пропуска — её перестают читать. Ничего не исправляет, только находит.
check_pair() — то же для разбора пары: контакты между картами в любую из
двух сторон, знаки по обеим картам, любые дома и знак «%» — находки.
Чистые функции, без базы и сети.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterator
from dataclasses import dataclass
from itertools import combinations

# Планета «на куспиде», если до куспида не дальше этого: шире — это уже натяжка.
CUSP_ORB = 2.0

# Сколько посторонних слов терпим между планетой и утверждением о ней:
# «Венера у тебя стоит во втором доме» — три.
MAX_FILL = 3

# Падежные формы имён → name_ru из расчёта. Только с заглавной: «солнце»
# со строчной в тексте — метафора, а не планета.
_PLANET_FORMS = {
    "Солнце": r"Солнц(?:е|а|у|ем)",
    "Луна": r"Лун(?:а|ы|е|у|ой|ою)",
    "Меркурий": r"Меркури(?:й|я|ю|ем|и)",
    "Венера": r"Венер(?:а|ы|е|у|ой)",
    "Марс": r"Марс(?:а|у|ом|е)?",
    "Юпитер": r"Юпитер(?:а|у|ом|е)?",
    "Сатурн": r"Сатурн(?:а|у|ом|е)?",
    "Уран": r"Уран(?:а|у|ом|е)?",
    "Нептун": r"Нептун(?:а|у|ом|е)?",
    "Плутон": r"Плутон(?:а|у|ом|е)?",
    "Хирон": r"Хирон(?:а|у|ом|е)?",
    "Асцендент": r"[Аа]сцендент(?:а|у|ом|е)?",
    "Середина неба": r"MC|МС",
}
_PLANET_RES = {name: re.compile(f"(?:{rx})") for name, rx in _PLANET_FORMS.items()}

# Двусловные имена склеиваются в один токен до разбора.
_MULTI = [
    (re.compile(r"[Сс]еверн\w*\s+узл\w*|[Сс]еверн\w*\s+узел|[Лл]унн\w*\s+узл\w*|[Лл]унн\w*\s+узел"), "Северный узел"),
    (re.compile(r"[Сс]еред[иы]н\w*\s+неба"), "Середина неба"),
]

_ANGLES = {"Асцендент", "Середина неба"}

# Знак в предложном падеже («в Овне») → sign_ru.
_SIGNS_PREP = {
    "Овне": "Овен", "Тельце": "Телец", "Близнецах": "Близнецы", "Раке": "Рак",
    "Льве": "Лев", "Деве": "Дева", "Весах": "Весы", "Скорпионе": "Скорпион",
    "Стрельце": "Стрелец", "Козероге": "Козерог", "Водолее": "Водолей", "Рыбах": "Рыбы",
}

# Порядковые числительные домов по основе; падеж решает окружение.
_ORD_STEMS = [
    ("одиннадцат", 11), ("двенадцат", 12), ("перв", 1), ("втор", 2), ("трет", 3),
    ("четвёрт", 4), ("четверт", 4), ("пят", 5), ("шест", 6), ("седьм", 7),
    ("восьм", 8), ("девят", 9), ("десят", 10),
]

_ASPECT_RES = {
    "соединение": re.compile(r"соединени\w*"),
    "оппозиция": re.compile(r"оппозици\w*"),
    "квадрат": re.compile(r"квадрат(?:а|у|ом|е|ы|ов|ам|ами|ах)?"),
    "трин": re.compile(r"трин(?:а|у|ом|е|ы|ов|ам|ами|ах)?"),
    "секстиль": re.compile(r"секстил(?:ь|я|ю|ем|е|и|ей|ям|ями|ях)"),
}

# Предлоги, после которых группа планет — дополнение без подлежащего
# («квадраты с Сатурном»): чьи квадраты, из соседних слов не понять.
_OBJECT_PREPS = {"с", "со", "к", "ко"}

_TOKEN_RE = re.compile(r"[^\W_]+(?:-[^\W_]+)*|[,.!?;:—()«»\n]")
_SENTENCE_END = {".", "!", "?", ";", "\n"}
_CLAUSE_BREAK = _SENTENCE_END | {":", "—", "(", ")", "«", "»"}


@dataclass(frozen=True, slots=True)
class Finding:
    """Подозрительное место: что сказано (quote), что не так (message), где в тексте."""

    kind: str  # house | sign | cusp | aspect | no_time | no_house | percent
    quote: str
    message: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class _Tok:
    text: str
    start: int
    end: int
    planet: str | None = None


def check(text: str, natal: dict, *, time_known: bool = True) -> list[Finding]:
    """Все подозрительные места черновика по порядку в тексте.

    natal — calc["natal"] карты заказа. При time_known=False дома и углы не
    сверяются, а любое их упоминание идёт находкой.
    """
    planets = {p["name_ru"]: p for p in natal["planets"]}
    if time_known:
        for key in ("asc", "mc"):
            angle = natal["angles"][key]
            planets[angle["name_ru"]] = angle
    cusps = {h["num"]: h["abs_deg"] for h in natal.get("houses", [])}
    aspects = {frozenset((a["p1_ru"], a["p2_ru"])): a for a in natal["aspects"]}

    found: list[Finding] = []
    for sent in _sentences(_tokens(text)):
        if not time_known:
            found += _no_time(text, sent)
        else:
            found += _houses(text, sent, planets)
            found += _cusps(text, sent, planets, cusps)
        found += _signs(text, sent, planets)
        found += _aspects(text, sent, planets, aspects)

    unique = {(f.kind, f.start, f.message): f for f in found}
    return sorted(unique.values(), key=lambda f: f.start)


# --- разбор текста -----------------------------------------------------------


def _tokens(text: str) -> list[_Tok]:
    """Слова и знаки препинания с позициями; имена планет распознаны."""
    spans: list[tuple[int, int, str]] = []
    taken: list[tuple[int, int]] = []
    for rx, name in _MULTI:
        for m in rx.finditer(text):
            spans.append((m.start(), m.end(), name))
            taken.append((m.start(), m.end()))

    toks: list[_Tok] = []
    for m in _TOKEN_RE.finditer(text):
        if any(a <= m.start() < b for a, b in taken):
            continue
        word = m.group()
        planet = next((n for n, rx in _PLANET_RES.items() if rx.fullmatch(word)), None)
        toks.append(_Tok(word, m.start(), m.end(), planet))
    toks += [_Tok(text[a:b], a, b, name) for a, b, name in spans]
    return sorted(toks, key=lambda t: t.start)


def _sentences(toks: list[_Tok]) -> list[list[_Tok]]:
    out: list[list[_Tok]] = [[]]
    for t in toks:
        out[-1].append(t)
        if t.text in _SENTENCE_END:
            out.append([])
    return [s for s in out if s]


def _ordinal(word: str) -> int | None:
    """«седьмом», «7-м», «7» → 7. None — не номер дома."""
    w = word.lower()
    digits = re.fullmatch(r"(\d{1,2})(?:-?(?:м|го|ом|й))?", w)
    if digits:
        n = int(digits.group(1))
        return n if 1 <= n <= 12 else None
    for stem, n in _ORD_STEMS:
        if w.startswith(stem):
            return n
    return None


def _aspect_kind(word: str) -> str | None:
    """«трине» → «трин»; None — не слово-аспект."""
    return next((k for k, rx in _ASPECT_RES.items() if rx.fullmatch(word.lower())), None)


def _group_before(sent: list[_Tok], i: int) -> tuple[list[_Tok], int]:
    """Группа планет перед позицией i («Сатурн и Уран»), через ≤ MAX_FILL слов.

    Возвращает планеты и индекс начала группы; пустой список — подлежащего нет.
    """
    j, fill = i - 1, 0
    while j >= 0 and sent[j].planet is None:
        if sent[j].text in _CLAUSE_BREAK or sent[j].text == "," or fill == MAX_FILL:
            return [], i
        fill += 1
        j -= 1
    group: list[_Tok] = []
    while j >= 0:
        if sent[j].planet:
            group.append(sent[j])
            j -= 1
        elif sent[j].text in ("и", ",") and j > 0 and sent[j - 1].planet:
            j -= 1
        else:
            break
    return group[::-1], j + 1


def _subject_before(sent: list[_Tok], i: int) -> tuple[list[_Tok], int]:
    """Подлежащее цепочки назад до границы придаточного, без предела слов.

    Группы после «к/с» пропускаются: в «Венера в секстиле к Нептуну и в
    трине к Сатурну» у трина подлежащее Венера, а не ближайший Нептун.
    """
    j = i - 1
    while j >= 0 and sent[j].text not in _CLAUSE_BREAK:
        if sent[j].planet is None:
            j -= 1
            continue
        group, g0 = _group_before(sent, j + 1)
        if g0 > 0 and sent[g0 - 1].text.lower() in _OBJECT_PREPS:
            j = g0 - 1
            continue
        return group, g0
    return [], i


def _group_after(sent: list[_Tok], i: int) -> tuple[list[_Tok], int, bool]:
    """Группа планет после позиции i: планеты, индекс за ней, был ли перед ней «с/к»."""
    j, fill, prep = i + 1, 0, False
    while j < len(sent) and sent[j].planet is None:
        if sent[j].text in _CLAUSE_BREAK or fill == MAX_FILL or _aspect_kind(sent[j].text):
            return [], j, prep
        prep = prep or sent[j].text.lower() in _OBJECT_PREPS
        fill += 1
        j += 1
    group: list[_Tok] = []
    while j < len(sent):
        if sent[j].planet:
            group.append(sent[j])
            j += 1
        elif sent[j].text in ("и", ",") and j + 1 < len(sent) and sent[j + 1].planet:
            # После «с/к» планета в начальной форме — уже новое подлежащее:
            # «в секстиле с Нептуном, Плутон в трине…» — Плутон не партнёр секстиля.
            nxt = sent[j + 1]
            if prep and nxt.text.lower() == nxt.planet.lower():
                break
            j += 1
        else:
            break
    return group, j, prep


def _quote(text: str, sent: list[_Tok], a: int, b: int) -> tuple[str, int, int]:
    start, end = sent[a].start, sent[min(b, len(sent) - 1)].end
    return text[start:end], start, end


# --- проверки ----------------------------------------------------------------


def _houses(text: str, sent: list[_Tok], planets: dict) -> list[Finding]:
    """«<планеты> … в седьмом доме» против house из расчёта."""
    out = []
    for i in range(len(sent) - 2):
        if sent[i].text.lower() not in ("в", "во") or not sent[i + 2].text.lower().startswith("дом"):
            continue
        num = _ordinal(sent[i + 1].text)
        if num is None:
            continue
        group, g0 = _group_before(sent, i)
        quote, s, e = _quote(text, sent, g0, i + 2)
        for t in group:
            real = planets.get(t.planet, {}).get("house")
            if t.planet in _ANGLES or real is None or real == num:
                continue
            out.append(Finding("house", quote, f"{t.planet} по расчёту в {real} доме, в тексте — в {num}", s, e))
    return out


def _cusps(text: str, sent: list[_Tok], planets: dict, cusps: dict[int, float]) -> list[Finding]:
    """«на куспиде / на пороге седьмого дома»: верно, только если до куспида ≤ CUSP_ORB."""
    out = []
    for i, t in enumerate(sent):
        low = t.text.lower()
        if not low.startswith(("куспид", "порог")):
            continue
        num = _ordinal(sent[i + 1].text) if i + 1 < len(sent) else None
        group, g0 = _subject_before(sent, i)
        quote, s, e = _quote(text, sent, min(g0, i), i + 2)
        if num is None or num not in cusps or not group:
            if low.startswith("куспид"):
                out.append(Finding("cusp", quote, "«куспид» — сверь с таблицей домов вручную", s, e))
            continue
        for p in group:
            deg = planets.get(p.planet, {}).get("abs_deg")
            if deg is None:
                continue
            dist = abs((deg - cusps[num] + 180) % 360 - 180)
            if dist > CUSP_ORB:
                out.append(
                    Finding("cusp", quote, f"{p.planet} в {dist:.1f}° от куспида {num} дома — не «на куспиде»", s, e)
                )
    return out


def _sign_claims(text: str, sent: list[_Tok]) -> Iterator[tuple[str, str, str, int, int]]:
    """Что текст говорит о знаках: (планета, знак, цитата, начало, конец)."""
    for i in range(len(sent) - 1):
        if sent[i].text.lower() not in ("в", "во") or sent[i + 1].text not in _SIGNS_PREP:
            continue
        sign = _SIGNS_PREP[sent[i + 1].text]
        group, g0 = _group_before(sent, i)
        quote, s, e = _quote(text, sent, g0, i + 1)
        for t in group:
            if t.planet is not None:
                yield t.planet, sign, quote, s, e


def _signs(text: str, sent: list[_Tok], planets: dict) -> list[Finding]:
    """«<планеты> … в Овне» против sign_ru из расчёта."""
    out = []
    for planet, sign, quote, s, e in _sign_claims(text, sent):
        real = planets.get(planet, {}).get("sign_ru")
        if real is None or real == sign:
            continue
        out.append(Finding("sign", quote, f"{planet} по расчёту в знаке {real}, в тексте — {sign}", s, e))
    return out


def _aspect_claims(
    text: str, sent: list[_Tok], known: Collection[str]
) -> Iterator[tuple[str, str, str, str, int, int]]:
    """Что текст говорит об аспектах: (планета, планета, тип, цитата, начало, конец).

    known — имена точек, которые есть в расчёте; пары с прочими пропускаются.
    """
    for i, t in enumerate(sent):
        kind = _aspect_kind(t.text)
        if kind is None:
            continue
        before, b0 = _subject_before(sent, i)
        g1, j, prep1 = _group_after(sent, i)
        g2, end, _ = _group_after(sent, j - 1) if g1 else ([], j, False)

        # «соединение Юпитера с МС»: обе стороны после слова — подлежащее не нужно.
        if g1 and g2 and not prep1:
            pairs = [(a, b) for a in g1 for b in g2]
        elif before and g1:
            pairs = [(a, b) for a in before for b in g1]
        elif before:
            pairs = list(combinations(before, 2))
        elif g1 and not prep1:
            pairs = list(combinations(g1, 2))
        else:
            continue

        last = end - 1 if g2 else j - 1
        quote, s, e = _quote(text, sent, min(b0, i), max(last, i))
        for a, b in pairs:
            if a.planet is None or b.planet is None or a.planet == b.planet:
                continue
            if a.planet not in known or b.planet not in known:
                continue
            yield a.planet, b.planet, kind, quote, s, e


def _aspects(text: str, sent: list[_Tok], planets: dict, aspects: dict) -> list[Finding]:
    """Слово-аспект и планеты вокруг: тип против списка аспектов расчёта."""
    out = []
    for a, b, kind, quote, s, e in _aspect_claims(text, sent, planets):
        real = aspects.get(frozenset((a, b)))
        if real is None:
            out.append(Finding("aspect", quote, f"аспекта {a} — {b} в расчёте нет", s, e))
        elif real["type_ru"] != kind:
            out.append(
                Finding(
                    "aspect", quote,
                    f"по расчёту {a} — {b}: {real['type_ru']} "
                    f"(орб {abs(real['orb']):.1f}°), в тексте — {kind}",
                    s, e,
                )
            )
    return out


def _no_time(text: str, sent: list[_Tok]) -> list[Finding]:
    """Время неизвестно: дома, асцендент и MC в тексте — выдумка от подставленного полудня."""
    out = []
    for i, t in enumerate(sent):
        low = t.text.lower()
        hit = (
            t.planet in _ANGLES
            or low.startswith("куспид")
            or (low.startswith("дом") and i and _ordinal(sent[i - 1].text) is not None)
        )
        if hit:
            quote, s, e = _quote(text, sent, max(i - 2, 0), i)
            out.append(Finding("no_time", quote, "время рождения неизвестно — домов и углов в карте нет", s, e))
    return out


# --- разбор пары --------------------------------------------------------------

_PERCENT = re.compile(r"\d+(?:[.,]\d+)?\s*%|%")


def check_pair(
    text: str,
    calc: dict,
    natal_a: dict,
    natal_b: dict,
    *,
    a_time_known: bool,
    b_time_known: bool,
) -> list[Finding]:
    """Подозрительные места черновика пары по порядку в тексте.

    calc — синастрия (compute_synastry), natal_a и natal_b — карты сторон.
    Чья планета, из текста не понять, поэтому контакт верен, если он есть в
    расчёте в любую из двух сторон, а знак — если так стоит хотя бы у одной.
    """
    sides = (_side_points(natal_a, a_time_known), _side_points(natal_b, b_time_known))
    known = set(sides[0]) | set(sides[1])
    contacts: dict[frozenset[str], set[str]] = {}
    for a in calc["aspects"]:
        contacts.setdefault(frozenset((a["a_ru"], a["b_ru"])), set()).add(a["type_ru"])
    # Луна стороны без времени приблизительна — её знак не сверяем.
    moon_unsure = not (a_time_known and b_time_known)
    no_axes = not (a_time_known or b_time_known)

    found: list[Finding] = []
    for sent in _sentences(_tokens(text)):
        found += _pair_houses(text, sent, no_axes=no_axes)
        for planet, sign, quote, s, e in _sign_claims(text, sent):
            real = [side[planet] for side in sides if planet in side]
            if not real or sign in real or (planet == "Луна" and moon_unsure):
                continue
            found.append(
                Finding("sign", quote, f"{planet} по расчёту: {' и '.join(real)}; в тексте — {sign}", s, e)
            )
        for a, b, kind, quote, s, e in _aspect_claims(text, sent, known):
            real_kinds = contacts.get(frozenset((a, b)))
            if real_kinds is None:
                found.append(Finding("aspect", quote, f"контакта {a} — {b} между картами нет", s, e))
            elif kind not in real_kinds:
                found.append(
                    Finding(
                        "aspect", quote,
                        f"между картами {a} — {b}: {', '.join(sorted(real_kinds))}; в тексте — {kind}",
                        s, e,
                    )
                )
    for m in _PERCENT.finditer(text):
        found.append(
            Finding("percent", m.group(), "процентов и оценок совместимости в разборе нет", m.start(), m.end())
        )

    unique = {(f.kind, f.start, f.message): f for f in found}
    return sorted(unique.values(), key=lambda f: f.start)


def _side_points(natal: dict, time_known: bool) -> dict[str, str]:
    """Точки одной стороны: имя → знак. Углы — только при известном времени."""
    points = {p["name_ru"]: p["sign_ru"] for p in natal["planets"]}
    if time_known:
        for key in ("asc", "mc"):
            angle = natal["angles"][key]
            points[angle["name_ru"]] = angle["sign_ru"]
    return points


def _pair_houses(text: str, sent: list[_Tok], *, no_axes: bool) -> list[Finding]:
    """Дома и куспиды в разборе пары — всегда находка: в синастрии их не считали.

    no_axes — времени нет у обеих сторон: тогда находка и асцендент с MC.
    """
    out = []
    for i, t in enumerate(sent):
        low = t.text.lower()
        house = low.startswith("куспид") or (
            low.startswith("дом") and i and _ordinal(sent[i - 1].text) is not None
        )
        if house:
            quote, s, e = _quote(text, sent, max(i - 2, 0), i)
            out.append(Finding("no_house", quote, "домов в расчёте пары нет — мы их не считали", s, e))
        elif no_axes and t.planet in _ANGLES:
            quote, s, e = _quote(text, sent, max(i - 2, 0), i)
            out.append(
                Finding("no_time", quote, "время рождения неизвестно у обоих — асцендента и MC нет", s, e)
            )
    return out
