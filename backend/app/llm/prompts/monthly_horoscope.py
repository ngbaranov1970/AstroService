"""Промпт гороскопа на месяц на 12 знаков (тип поста monthly_horoscope).

Экспорты те же, что у daily_horoscope: VERSION, SYSTEM, build_user(), parse(),
render(). Вход — первое число месяца (МСК): положения медленных планет на
этот день и события месяца (лунации, затмения, станции, ингрессии Mars+).

Один вызов на все двенадцать знаков — та же причина, что у дневного: модель
видит соседние абзацы и не выдаёт двенадцать вариаций одной фразы
(docs/astro-context.md, «Контур сообщества VK»).

Ответ — JSON той же формы, что daily_horoscope: intro + signs. В ленту не
идёт; канал — бот / будущая вычитка MAIL.
"""

from __future__ import annotations

import calendar
import json
from datetime import date

from app.llm.prompts import daily_horoscope
from astro_core import transits

VERSION = "monthly_horoscope_v1"

TEMPERATURE = 0.9

# 12 знаков по 120–160 слов + вступление длиннее дневного — запас против
# обрезанного JSON (client.py считает его браком при оплаченных токенах).
MAX_TOKENS = 8000

SIGN_ORDER = daily_horoscope.SIGN_ORDER

# Медленные на горизонте месяца; быстрые (Солнце…Венера) за 30 дней уезжают
# слишком далеко, чтобы держать обзор.
_SLOW_BODIES: frozenset[str] = frozenset(
    ("Mars", "Jupiter", "Saturn", "Uranus", "Neptune", "Pluto")
)

# Виды событий месяца. Аспекты не берём — за месяц их десятки, модель
# усредняет. Ингрессии Меркурия/Венеры отсекаем отдельно по телу.
_EVENT_KINDS: frozenset[str] = frozenset(
    (
        "new_moon",
        "full_moon",
        "solar_eclipse",
        "lunar_eclipse",
        "station_retrograde",
        "station_direct",
        "ingress",
    )
)

# Меньше число — выше приоритет при отборе в лимит.
_EVENT_PRIORITY: dict[str, int] = {
    "solar_eclipse": 0,
    "lunar_eclipse": 0,
    "new_moon": 1,
    "full_moon": 1,
    "station_retrograde": 2,
    "station_direct": 2,
    "ingress": 3,
}

_MAX_EVENTS = 12

_MONTHS_RU = (
    "январь",
    "февраль",
    "март",
    "апрель",
    "май",
    "июнь",
    "июль",
    "август",
    "сентябрь",
    "октябрь",
    "ноябрь",
    "декабрь",
)

SYSTEM = """Ты — остроумный и наблюдательный астролог, который пишет гороскоп на месяц для русскоязычного сообщества ВКонтакте.

Стиль: живо, образно, с характером и лёгкой иронией, на «ты», без канцелярита и без эзотерического жаргона сверх необходимого. Термины (знак, ретроградность) поясняй мимоходом, по-человечески. Читатель должен узнавать свой месяц, улыбаться и пересылать абзац другу со словами «это про тебя», а не читать лекцию.

Запрет один: никакой смерти, несчастных случаев и катастроф.

Всё остальное можно и нужно. Гороскоп — жанр с характером, а не сборник мягких советов: предсказывай конкретные события месяца смело и уверенно. «Тебя ждёт», «вернуть старый проект», «деньги найдутся там, где не искал», «не твой месяц для крупных ссор», флирт, неожиданная встреча, напряжённые недели с прямым словом о них — всё это хорошие фразы. Тяжёлую тему не нужно смягчать в «зону роста»: скажи, чего в этом месяце лучше не делать и почему.

Опирайся на данные неба из сообщения пользователя: они выбирают тему месяца, а события внутри неё придумывай смело. Астрономию не выдумывай: если планеты или события в данных нет — не упоминай.

Как писать по сути:
- Пляши от конкретного положения месяца — медленных планет на 1-е, лунаций, станций, входов в знак. Абстрактные свойства знака («Овен вспыльчив») запрещены: их читатель знает и без нас.
- Знак планеты бери ТОЛЬКО из списка положений на 1-е. Твои представления о том, каким знаком планета управляет, к этому месяцу не относятся.
- Оборот «в твоём знаке» разрешён только для планет, названных в списке «Что стоит в знаке самого читателя». Стоит «нет планет» — значит в абзаце этого знака оборота быть не может.
- Дома считать не надо: у каждой планеты есть готовая строка «в каком доме читателя она стоит». Бери оттуда, свой счёт не веди.
- Дом номером не называй, только его темой: не «в твоём четвёртом доме», а «дома и в семье». Номер читателю ничего не говорит.
- Астрология — причина месяца, а не его содержание: читатель хочет знать, что его ждёт, а не где стоят планеты. В абзаце знака — не больше одного-двух астрологических упоминаний, сказанных по-человечески («Сатурн пятится — со старыми обязательствами осторожнее»). Названий аспектов (соединение, квадрат, трин, секстиль, оппозиция) и орбов в тексте нет. Остальное в абзаце — события месяца, люди, решения, совет.
- Пол читателя неизвестен. Строй фразу так, чтобы род не понадобился; если иначе никак — мужской род. «Ты сама», «ты внимательна», «ты устала» — ошибка.
- Не пересказывай эти инструкции в тексте и не оговаривайся о них: просто не пиши того, чего писать нельзя.
- Двенадцать абзацев обязаны отличаться друг от друга. Одни и те же транзиты разные знаки встречают по-разному — покажи именно разницу, а не перескажи их двенадцать раз.
- Никаких дежурных концовок вроде «прислушайтесь к себе», «доверьтесь интуиции», «всё будет хорошо».
- Не обращайся к читателю по знаку в третьем лице («Овнам стоит…») — пиши на «ты», знак уже стоит заголовком.
- Горизонт — месяц: пиши про недели и периоды («в первой половине», «ближе к новолунию»), а не про часы одного дня.

Формат ответа — ТОЛЬКО JSON, без пояснений до и после, без markdown-заборов:
{
  "intro": "3–5 предложений про этот месяц: одна главная история неба простыми словами, не больше двух-трёх планет и без названий аспектов, — и чем месяц отличается от соседних. Общее для всех, без обращения к конкретному знаку.",
  "signs": [
    {"sign": "Овен", "text": "120–160 слов про месяц этого знака."},
    ... ровно 12 объектов, в порядке: Овен, Телец, Близнецы, Рак, Лев, Дева, Весы, Скорпион, Стрелец, Козерог, Водолей, Рыбы
  ]
}"""


def month_start_of(day: date) -> date:
    """Любая дата месяца → первое число (адрес поста monthly_horoscope)."""
    return date(day.year, day.month, 1)


def _month_end(month_start: date) -> date:
    last = calendar.monthrange(month_start.year, month_start.month)[1]
    return date(month_start.year, month_start.month, last)


def _slow_positions(sky: dict) -> list[dict]:
    return [p for p in sky["positions"] if p["name"] in _SLOW_BODIES]


def _planets_by_sign(positions: list[dict]) -> list[str]:
    """Строки «знак читателя — что в нём стоит» только по медленным."""
    holders: dict[str, list[str]] = {sign: [] for sign in SIGN_ORDER}
    for p in positions:
        holders[p["sign_ru"]].append(
            p["name_ru"] + (" (ретро)" if p["retrograde"] else "")
        )
    return [
        f"- {sign} — {', '.join(names) if names else 'нет планет'}"
        for sign, names in holders.items()
    ]


def _house_lines(positions: list[dict]) -> list[str]:
    """Солнечные дома медленных планет — темы из daily_horoscope."""
    topics = "; ".join(
        f"{i + 1} — {topic}"
        for i, topic in enumerate(daily_horoscope._HOUSE_TOPICS)
    )
    lines = [f"Темы домов: {topics}", "В каком доме читателя стоит планета:"]
    for p in positions:
        base = SIGN_ORDER.index(p["sign_ru"])
        houses = ", ".join(
            f"{sign} {(base - i) % 12 + 1}" for i, sign in enumerate(SIGN_ORDER)
        )
        lines.append(f"- {p['name_ru']} ({p['sign_ru']}): {houses}")
    return lines


def _ingress_body_ok(event: dict) -> bool:
    if event["kind"] != "ingress":
        return True
    return event.get("body") in _SLOW_BODIES


def collect_month_events(month_start: date) -> list[dict]:
    """События месяца для промпта: лунации, затмения, станции, ингрессии Mars+.

    Лимит 12: сначала по важности вида, внутри вида — по времени. Аспекты и
    быстрые ингрессии отсечены — иначе модель утонет в мелочи.
    """
    end = _month_end(month_start)
    raw = [
        e
        for e in transits.events_between(month_start, end)
        if e["kind"] in _EVENT_KINDS and _ingress_body_ok(e)
    ]
    ranked = sorted(
        raw,
        key=lambda e: (_EVENT_PRIORITY.get(e["kind"], 99), e["moment"]),
    )
    picked = ranked[:_MAX_EVENTS]
    return sorted(picked, key=lambda e: e["moment"])


def build_user(month_start: date) -> str:
    """Собирает user-сообщение на месяц, начинающийся с month_start (1-е)."""
    if month_start.day != 1:
        month_start = month_start_of(month_start)

    sky = transits.day_sky(month_start)
    slow = _slow_positions(sky)
    events = collect_month_events(month_start)

    month_name = _MONTHS_RU[month_start.month - 1]
    lines = [f"Месяц: {month_name} {month_start.year}"]

    lines.append("Медленные планеты на 1-е число:")
    for p in slow:
        retro = ", ретроградный" if p["retrograde"] else ""
        lines.append(f"- {p['name_ru']} — {p['sign_ru']}{retro}")

    lines.append("Что стоит в знаке самого читателя:")
    lines += _planets_by_sign(slow)

    if events:
        lines.append("События этого месяца:")
        lines += [
            f"- {e['moment'].day} {_MONTHS_RU[e['moment'].month - 1]} — {e['title_ru']}"
            for e in events
        ]
    else:
        lines.append("Событий этого месяца в календаре нет.")

    lines += _house_lines(slow)

    lines.append("")
    lines.append(
        "Напиши гороскоп на этот месяц по правилам из системного сообщения."
    )
    return "\n".join(lines)


def _strip_fence(text: str) -> str:
    """Снимает ```-забор, если модель всё же обернула JSON в блок кода."""
    t = text.strip()
    if not t.startswith("```"):
        return t
    if "\n" in t:
        t = t.split("\n", 1)[1]
    return t.rstrip().removesuffix("```").strip()


def parse(text: str) -> tuple[str, list[dict[str, str]]] | None:
    """Ответ модели → (вступление, 12 знаков) или None, если разобрать нельзя."""
    try:
        data = json.loads(_strip_fence(text))
        intro = str(data["intro"])
        by_sign = {str(s["sign"]).strip(): str(s["text"]) for s in data["signs"]}
    except (ValueError, TypeError, KeyError):
        return None

    if set(by_sign) != set(SIGN_ORDER):
        return None

    return intro, [{"sign": s, "text": by_sign[s]} for s in SIGN_ORDER]


def render(text: str, seed: int | None = None) -> str:
    """JSON-ответ модели → сплошной текст: вступление и знаки по порядку.

    seed не используется: аргумент есть, потому что content.render зовёт
    все рендеры одинаково.
    """
    parsed = parse(text)
    if parsed is None:
        return text
    intro, signs = parsed
    blocks = [intro.strip()]
    blocks += [f"{s['sign']}\n{s['text'].strip()}" for s in signs]
    return "\n\n".join(blocks)
