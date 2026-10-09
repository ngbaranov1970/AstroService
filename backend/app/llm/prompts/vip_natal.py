"""Промпт черновика VIP-разбора натальной карты (тип заказа vip_natal).

Отдельный модуль, а не версия full_natal: тексты full_natal открываются
в Mini App, и бамп его VERSION с ослабленными правилами перегенерировал бы
их там. Здесь под запретом только смерть — текст вычитывает владелец
и в приложение он не попадает (docs/astro-prompts-v2.md, раздел 9).

Рамка задана первым и последним блоком, середину и заголовки выбирает
модель. Ответ — JSON-список секций; to_draft() превращает его в простой
текст для orders.draft_text, который владелец правит руками.
"""

from __future__ import annotations

import json
import shutil
import textwrap

from app.models import ClientGender

VERSION = "vip_natal_v2"

# pro на 1.0 выбран на приёмке 09.10.2026: flash сыпал терминами и орбами,
# pro на 0.8 писал два длинных блока про одно и то же.
MODEL = "deepseek-v4-pro"
TEMPERATURE = 1.0

# Оборванный JSON не разбирается вовсе: на приёмке ответ доходил до 3461
# токена из 4000, поэтому запас вместо экономии.
MAX_TOKENS = 8000

FIRST_TITLE = "Главное в двух словах"
LAST_TITLE = "Что я тебе советую"

SYSTEM = f"""Ты — астролог с двадцатилетним стажем. Ты готовишь личный разбор
натальной карты для конкретного человека, который за него заплатил. Разбор
должен быть максимально правдивым.
# Вход
Ты получишь расчёт натальной карты: позиции планет по знакам и домам,
углы, куспиды домов с управителями (у каждого управителя указано, в каком
знаке и доме он стоит), аспекты с орбисами, пол человека.
Расчёт уже сделан — не пересчитывай, не сомневайся в данных, не проси
уточнений.

Если в расчёте сказано, что время рождения неизвестно, — домов, асцендента
и MC нет. Не упоминай их и не оговаривай отсутствие времени.

# Правдивость
Аспекты бери только из переданного списка. Если между двумя планетами
аспекта в списке нет — его нет и в карте. Выдуманный аспект человек
проверит на любом сайте за минуту, и весь разбор после этого не стоит
ничего.

Не пиши, что планета стоит на куспиде или «на пороге» дома, если этого
нет в данных. Начало знака — не начало дома: планета в первых градусах
знака не стоит на куспиде, если куспид дома лежит в предыдущем знаке.

Управитель дома и планета в доме — разное. Дом планеты бери из списка
планет, а не из того, каким домом она управляет: управитель второго дома,
стоящий в десятом, находится в десятом доме, а не во втором.

Найди в карте самые сильные ресурсные места — тесные гармоничные аспекты,
сильные позиции — и скажи о них так же конкретно и с привязкой к карте,
как о напряжении. Ресурс идёт рядом с напряжением, а не вместо него:
острые места не смягчай.

# Голос
Обращение на «ты». Род глаголов и прилагательных — по полу из расчёта
(«ты родилась», «ты склонен»). Утверждения, а не гипотезы. Без «важно
отметить», «стоит помнить». Связный текст без списков, без markdown и без
эмодзи.

# Структура
Первый блок — «{FIRST_TITLE}»: несколько предложений о сути карты,
синтез, а не перечень планет.
Последний блок — «{LAST_TITLE}»: советы, каждый из которых вытекает из
сказанного выше.
Между ними — блоки на твой выбор. Каждый блок — про одну тему жизни
(характер, гнев, семья, любовь, работа, друзья, деньги, тело и так далее),
а не про одну планету: планеты и аспекты — доказательства внутри темы.
Бери темы, где в этой карте самое сильное и самое острое. Блоки короткие:
одна тема — один-два абзаца; лучше больше коротких блоков, чем мало
длинных. Заголовок блока придумываешь сам, и он называет суть
(«Дом и семья: самая больная и самая важная тема»).

# Формат ответа
Верни ТОЛЬКО валидный JSON, без markdown-обёртки и без текста до и после:

{{"sections": [{{"title": "...", "body": "..."}}, ...]}}

Абзацы внутри body разделяй переводом строки. Правила этого сообщения
в текст разбора не попадают: человек читает разбор, а не инструкцию."""


# Род в тексте зависит от этой строки; у заказов до 09.10.2026 пола нет.
_GENDER_LINES = {
    ClientGender.FEMALE: "Пол: женский — пиши в женском роде.",
    ClientGender.MALE: "Пол: мужской — пиши в мужском роде.",
}

# Без времени углы и их аспекты посчитаны от подставленного полудня — выдуманы.
_ANGLES_RU = {"Асцендент", "Середина неба"}

_NO_TIME_NOTE = (
    "Время рождения неизвестно: асцендента, MC и домов в этой карте нет. "
    "Не упоминай их и не оговаривай отсутствие времени."
)


def _dm(deg: float) -> str:
    """20.6874 → «20°41'». Копия full_natal._dm: модули промптов самостоятельны."""
    d = int(deg)
    m = round((deg - d) * 60)
    if m == 60:  # 29.9999 не должно превращаться в «29°60'»
        d, m = d + 1, 0
    return f"{d}°{m:02d}'"


def _house_line(h: dict) -> str:
    """«- 2 дом — Лев 20°00' (управитель Солнце — Стрелец, 5 дом)»."""
    line = f"- {h['num']} дом — {h['sign_ru']} {_dm(h['deg_in_sign'])}"
    rulers = h.get("rulers")
    if not rulers:
        return line
    word = "управитель" if len(rulers) == 1 else "управители"
    where = "; ".join(f"{r['name_ru']} — {r['sign_ru']}, {r['house']} дом" for r in rulers)
    return f"{line} ({word} {where})"


def _planet_line(p: dict, with_house: bool) -> str:
    """«Солнце — Рак 22°05', 11 дом» (+ пометка ретроградности)."""
    parts = [f"{p['name_ru']} — {p['sign_ru']} {_dm(p['deg_in_sign'])}"]
    if with_house and p.get("house"):
        parts.append(f"{p['house']} дом")
    if p.get("retrograde"):
        parts.append("ретроградный")
    return ", ".join(parts)


def build_user(natal: dict, gender: ClientGender | None, *, time_known: bool = True) -> str:
    """Собирает user-сообщение из результата compute_natal.

    Имя в промпт не идёт, как и в остальных натальных разборах. При
    time_known=False домов, углов и аспектов к углам в сообщении нет.
    """
    lines = chart_block(natal, gender, time_known=time_known)
    lines.append("")
    lines.append(
        "Напиши личный разбор этой натальной карты по правилам из системного "
        "сообщения. Верни только JSON, без обёртки markdown."
    )
    return "\n".join(lines)


def chart_block(natal: dict, gender: ClientGender | None, *, time_known: bool = True) -> list[str]:
    """Строки расчёта карты: пол, планеты, углы, дома, аспекты.

    Публичная, потому что её зовёт vip_edit: правка обязана видеть карту
    ровно так, как её видел черновик.
    """
    lines: list[str] = []
    if gender is not None:
        lines.append(_GENDER_LINES[gender])

    lines.append("Планеты:")
    lines += [f"- {_planet_line(p, with_house=time_known)}" for p in natal["planets"]]

    if time_known:
        angles = natal["angles"]
        lines.append("Углы:")
        lines.append(
            f"- Асцендент — {angles['asc']['sign_ru']} {_dm(angles['asc']['deg_in_sign'])}"
        )
        lines.append(
            f"- Середина неба — {angles['mc']['sign_ru']} {_dm(angles['mc']['deg_in_sign'])}"
        )
        lines.append("Дома (куспиды, Placidus) и их управители:")
        lines += [_house_line(h) for h in natal["houses"]]
    else:
        lines.append(_NO_TIME_NOTE)

    aspects = natal["aspects"]
    if not time_known:
        aspects = [a for a in aspects if not {a["p1_ru"], a["p2_ru"]} & _ANGLES_RU]
    # Все аспекты расчёта, без предела full_natal в 20: он отрезал Юпитер на MC (орб 5.7°).
    aspects = sorted(aspects, key=lambda a: abs(a["orb"]))
    if aspects:
        lines.append("Аспекты (по тесноте орба):")
        lines += [
            f"- {a['p1_ru']} {a['type_ru']} {a['p2_ru']} (орб {abs(a['orb']):.1f}°)"
            for a in aspects
        ]
    return lines


def _strip_fence(text: str) -> str:
    """Снимает ```json-забор: запрет в SYSTEM соблюдается не всегда."""
    t = text.strip()
    if not t.startswith("```"):
        return t
    if "\n" in t:
        t = t.split("\n", 1)[1]
    return t.rstrip().removesuffix("```").strip()


def parse(text: str) -> list[dict[str, str]] | None:
    """Ответ модели → [{"title": ..., "body": ...}] или None, если не разобрать.

    None — сигнал показать сырой ответ: брак при приёмке важнее увидеть
    глазами, чем получить traceback.
    """
    try:
        sections = json.loads(_strip_fence(text))["sections"]
        return [{"title": str(s["title"]), "body": str(s["body"])} for s in sections]
    except (ValueError, TypeError, KeyError):
        return None


def to_draft(sections: list[dict[str, str]]) -> str:
    """Секции → простой текст для orders.draft_text: заголовок, пустая строка, абзацы.

    Markdown не используем: черновик правят руками, а VK его не рисует.
    """
    blocks = []
    for s in sections:
        paragraphs = [p.strip() for p in s["body"].split("\n") if p.strip()]
        blocks.append("\n\n".join([s["title"].strip(), *paragraphs]))
    return "\n\n\n".join(blocks)


def render(text: str) -> str:
    """JSON-ответ модели → текст для терминала (scripts/report_cli.py).

    Рядом с заголовком — длина блока в словах: рамок объёма в SYSTEM нет,
    и на приёмке видно, где модель недобирает.
    """
    sections = parse(text)
    if sections is None:
        return text
    width = min(shutil.get_terminal_size(fallback=(88, 24)).columns, 100)
    out = []
    for s in sections:
        body = "\n\n".join(
            textwrap.fill(p.strip(), width=width) for p in s["body"].split("\n") if p.strip()
        )
        out.append(f"## {s['title']}  ({len(s['body'].split())} сл.)\n\n{body}")
    return "\n\n\n".join(out)
