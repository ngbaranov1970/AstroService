"""Картинка к посту в ленту: «небо дня» одной карточкой 1080×1080.

Место в цепочке: astro_core/transits.py считает небо, llm/prompts/daily_sky.py
называет главное событие суток, этот модуль рисует по ним PNG. Базы здесь
нет — на входе дата и строки «Стоит» / «Подождёт», на выходе байты; достаёт
их из поста web/routes/admin.py.

Заголовок собирается из данных, а не у LLM: карточка обязана совпадать с
зачином поста. Раскладка повторяет макет daily_card.svg.j2 — координаты по
базовой линии текста, как в SVG.
"""

from __future__ import annotations

import io
import re
from datetime import date
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.llm.prompts.daily_sky import main_event
from astro_core import transits

FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"

SIZE = 1080

BG = (23, 19, 16)
FRAME = (138, 114, 72)
GOLD = (201, 162, 94)
CREAM = (236, 229, 216)

_WEEKDAYS_RU = (
    "Понедельник", "Вторник", "Среда", "Четверг",
    "Пятница", "Суббота", "Воскресенье",
)
_MONTHS_RU = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)

# Раскладка из макета: заголовок, тело, отступы блоков, нижняя граница текста.
TITLE_Y, TITLE_MAX_W = 282, 820
LABEL_X, TEXT_X, TEXT_MAX_W = 130, 320, 610
BLOCK_GAP = 50
CONTENT_LIMIT = 872

# Звёзды фона — фиксированные, как в макете: (x, y, радиус, вид).
_STARS = (
    (96, 112, 1.6, "dot"), (178, 78, 7, "spark"), (318, 104, 1.2, "dot"),
    (748, 86, 1.4, "dot"), (902, 118, 8, "spark"), (986, 214, 1.3, "dot"),
    (82, 402, 1.2, "dot"), (1000, 520, 6, "spark"), (74, 690, 6, "spark"),
    (1002, 812, 1.4, "dot"), (152, 996, 1.3, "dot"), (872, 1000, 7, "spark"),
)

# Фигуры рисуем в 4× и уменьшаем: Pillow не сглаживает многоугольники и
# эллипсы, и ромб с полумесяцем выходят зубчатыми.
_SS = 4


@lru_cache
def _font(name: str, size: int, weight: int) -> ImageFont.FreeTypeFont:
    """Переменный шрифт с выставленной насыщенностью (font-weight из макета)."""
    f = ImageFont.truetype(str(FONTS_DIR / name), size)
    f.set_variation_by_axes([weight])
    return f


# --- текст ----------------------------------------------------------------


def _wrap(text: str, f: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Перенос по словам в пределах max_width пикселей.

    Тире и слова в одну-две буквы (предлоги, союзы) привязаны неразрывным
    пробелом к соседу: «в» в конце строки и тире в начале читаются как брак.
    """
    text = re.sub(r" ([—–])", "\u00a0\\1", text)
    text = re.sub(r"(?<!\w)(\w{1,2}) ", "\\1\u00a0", text)
    lines, cur = [], ""
    for word in text.split(" "):
        candidate = f"{cur} {word}".strip()
        if not cur or f.getlength(candidate) <= max_width:
            cur = candidate
        else:
            lines.append(cur)
            cur = word
    lines.append(cur)
    return lines


def _balanced(text: str, f: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Тот же перенос, но строки одной длины: без короткого хвоста в одно слово.

    Сужаем ширину, пока число строк не вырастет, — последняя удачная ширина
    делит текст ровнее всего.
    """
    count = len(_wrap(text, f, max_width))
    width = max_width
    while width > 200 and len(_wrap(text, f, width - 10)) == count:
        width -= 10
    return _wrap(text, f, width)


def _fit_title(s: str) -> tuple[list[str], ImageFont.FreeTypeFont, int]:
    """Заголовок в 1–2 строки: (строки, шрифт, межстрочное расстояние).

    Кегль 84 по макету; не влезает в две строки — уменьшаем, третья строка
    сдвинула бы блок «Стоит / Подождёт» за подвал.
    """
    for size in range(84, 55, -4):
        f = _font("Cormorant.ttf", size, 500)
        lines = _balanced(s, f, TITLE_MAX_W)
        if len(lines) <= 2:
            break
    return lines, f, round(size * 88 / 84)


def _capitalize(s: str) -> str:
    # Модель пишет do/wait со строчной — в тексте они идут после «Стоит:».
    return s[:1].upper() + s[1:]


def headline(event: dict | None, moon: dict) -> str:
    """Заголовок карточки — короткая форма главного события суток.

    title_ru из ядра берём только там, где он и так краток. «Уран становится
    ретроградным в Близнецах» пришлось бы ужать до нечитаемого кегля.
    """
    if event is None:
        return f"Луна {moon['sign_loc']}"

    kind = event["kind"]
    if kind == "moon_ingress":
        return f"Луна уходит в {event['sign_ru']}"
    if kind == "ingress":
        return f"{event['body_ru']} уходит в {event['sign_ru']}"
    if kind == "station_retrograde":
        return f"{event['body_ru']} разворачивается"
    if kind == "station_direct":
        return f"{event['body_ru']} идёт прямо"
    return event["title_ru"]


def subtitle(moon: dict, event: dict | None) -> str:
    """Золотая строка под заголовком: время события, а без события — фаза Луны."""
    if event is None:
        return f"{moon['phase_ru']}, {moon['illumination']}%"
    return f"в {event['moment']:%H:%M} по Москве"


# --- рисование ------------------------------------------------------------


def _star(draw: ImageDraw.ImageDraw, x: float, y: float, r: float, fill) -> None:
    """Четырёхлучевая искра: ромб с вдавленными к центру сторонами."""
    k = r * 0.18
    draw.polygon(
        [(x, y - r), (x + k, y - k), (x + r, y), (x + k, y + k),
         (x, y + r), (x - k, y + k), (x - r, y), (x - k, y - k)],
        fill=fill,
    )


def _ornament(divider_y: int | None) -> Image.Image:
    """Слой фигур без текста: рамка, звёзды, разделитель, полумесяц подвала."""
    s = _SS
    layer = Image.new("RGBA", (SIZE * s, SIZE * s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    draw.rectangle((44 * s, 44 * s, 1035 * s, 1035 * s), outline=FRAME, width=round(1.5 * s))

    for x, y, r, kind in _STARS:
        if kind == "spark":
            _star(draw, x * s, y * s, r * s, GOLD + (140,))
        else:
            draw.ellipse(((x - r) * s, (y - r) * s, (x + r) * s, (y + r) * s), fill=GOLD + (128,))

    # Разделитель: две линии и ромб между ними. None — под ним нечего отделять.
    if divider_y is not None:
        y = divider_y * s
        for x0, x1 in ((430, 522), (558, 650)):
            draw.line((x0 * s, y, x1 * s, y), fill=FRAME, width=round(1.5 * s))
        draw.polygon(
            [(540 * s, y - 8 * s), (546 * s, y), (540 * s, y + 8 * s), (534 * s, y)], fill=GOLD
        )

    # Полумесяц — два круга, второй цветом фона: цветные эмодзи Pillow не рисует.
    draw.ellipse(((434 - 14) * s, (942 - 14) * s, (434 + 14) * s, (942 + 14) * s), fill=GOLD)
    draw.ellipse(((441 - 12) * s, (937 - 12) * s, (441 + 12) * s, (937 + 12) * s), fill=BG)

    return layer.resize((SIZE, SIZE), Image.Resampling.LANCZOS)


def _draw_tracked(draw: ImageDraw.ImageDraw, x: float, y: int, s: str, f, fill, tracking: int) -> None:
    """Строка вразрядку от x по базовой линии: letter-spacing у Pillow нет."""
    for ch in s:
        draw.text((x, y), ch, font=f, fill=fill, anchor="ls")
        x += draw.textlength(ch, font=f) + tracking


def _tracked_width(draw: ImageDraw.ImageDraw, s: str, f, tracking: int) -> float:
    return sum(draw.textlength(ch, font=f) for ch in s) + tracking * (len(s) - 1)


def _layout_body(
    blocks: list[tuple[str, str]], top: int
) -> tuple[list, ImageFont.FreeTypeFont, int, int]:
    """Раскладка «Стоит / Подождёт»: ([(подпись, y, строки)], шрифт, шаг строк, низ текста).

    Кегль 34 по макету; не влезает над подвалом (y 872) — уменьшаем до 26,
    иначе текст наедет на подпись сообщества.
    """
    for size in range(34, 25, -2):
        f = _font("Jost.ttf", size, 400)
        line_h = round(size * 46 / 34)
        y, placed = top, []
        for label, text in blocks:
            lines = _balanced(_capitalize(text), f, TEXT_MAX_W)
            placed.append((label, y, lines))
            y += line_h * len(lines) + BLOCK_GAP
        bottom = y - BLOCK_GAP - line_h if placed else top
        if bottom <= CONTENT_LIMIT:
            break
    return placed, f, line_h, bottom


def build(day: date, moon: dict, event: dict | None, do: str = "", wait: str = "") -> Image.Image:
    """Карточка целиком. moon и event — из day_sky() и main_event().

    Пустые do и wait не печатаются: у брака разбора их нет, а подписи без
    текста выглядели бы ошибкой вёрстки.
    """
    title_lines, title_font, title_lh = _fit_title(headline(event, moon))
    title_last = TITLE_Y + title_lh * (len(title_lines) - 1)
    time_y = title_last + 56
    divider_y = time_y + 50

    blocks = [(label, text) for label, text in (("Стоит", do), ("Подождёт", wait)) if text]
    placed, body_font, line_h, bottom = _layout_body(blocks, divider_y + 72)

    # Короткий текст оставляет низ пустым — сдвигаем всю композицию к центру.
    shift = max(0, (CONTENT_LIMIT - bottom) // 2)

    img = Image.new("RGB", (SIZE, SIZE), BG)
    ornament = _ornament(divider_y + shift if placed else None)
    img.paste(ornament, (0, 0), ornament)
    draw = ImageDraw.Draw(img)

    date_line = f"{_WEEKDAYS_RU[day.weekday()]}, {day.day} {_MONTHS_RU[day.month - 1]}"
    draw.text((540, 172 + shift), date_line, font=_font("Cormorant-Italic.ttf", 38, 500),
              fill=GOLD, anchor="ms")

    for i, line in enumerate(title_lines):
        draw.text((540, TITLE_Y + title_lh * i + shift), line, font=title_font,
                  fill=CREAM, anchor="ms")

    draw.text((540, time_y + shift), subtitle(moon, event), font=_font("Jost.ttf", 30, 400),
              fill=GOLD, anchor="ms")

    label_font = _font("Cormorant-Italic.ttf", 42, 500)
    for label, y, lines in placed:
        draw.text((LABEL_X, y + shift), label, font=label_font, fill=GOLD, anchor="ls")
        for i, line in enumerate(lines):
            draw.text((TEXT_X, y + line_h * i + shift), line, font=body_font,
                      fill=CREAM, anchor="ls")

    # Подвал стоит на месте при любом сдвиге: он привязан к рамке, а не к тексту.
    _draw_tracked(draw, 460, 952, "ЗВЕЗДОЧЁТ", _font("Jost.ttf", 26, 400), GOLD, 7)
    url_font = _font("Jost.ttf", 20, 400)
    url = "vk.ru/astrozvezdochet"
    _draw_tracked(draw, 540 - _tracked_width(draw, url, url_font, 1) / 2, 990, url,
                  url_font, FRAME, 1)

    return img


def render(day: date, do: str = "", wait: str = "") -> bytes:
    """PNG карточки этого дня. Небо считает сама — 20 мс, кеш не нужен."""
    sky = transits.day_sky(day)
    img = build(day, sky["moon"], main_event(sky), do, wait)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
