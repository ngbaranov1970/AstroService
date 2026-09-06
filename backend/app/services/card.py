"""Картинка к посту в ленту: «небо дня» одной карточкой 1080×1080.

Место в цепочке: astro_core/transits.py считает небо, llm/prompts/daily_sky.py
называет главное событие суток, этот модуль рисует по ним PNG. Базы здесь
нет — на входе дата, на выходе байты; кто и зачем просит картинку, решает
web/routes/admin.py.

Подписи собираются из данных, а не у LLM: карточка обязана совпадать с
текстом поста, а второй генератор разошёлся бы с ним на первой же правке
промпта.
"""

from __future__ import annotations

import io
import random
from datetime import date
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont

from app.llm.prompts.daily_sky import main_event
from astro_core import transits

FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"

SIZE = 1080

BG = (23, 17, 14)
GOLD = (176, 141, 90)
GOLD_DIM = (120, 96, 61)
GOLD_LIGHT = (214, 168, 94)
CREAM = (245, 239, 230)
# Освещённая часть диска — чуть светлее фона: без заливки полнолуние
# выглядит пустым контуром.
LIT_FILL = (29, 22, 18)

_WEEKDAYS_RU = (
    "Понедельник", "Вторник", "Среда", "Четверг",
    "Пятница", "Суббота", "Воскресенье",
)
_MONTHS_RU = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)

# Прямоугольники, занятые текстом: искорки в них не ставим.
_BUSY = (
    (250, 125, 830, 210),
    (415, 240, 665, 490),
    (120, 515, 960, 660),
    (290, 655, 790, 730),
    (320, 885, 760, 965),
)


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS_DIR / name), size)


# --- текст ----------------------------------------------------------------


def _advances(draw: ImageDraw.ImageDraw, s: str, f, tracking: int) -> list[float]:
    """Шаг после каждого символа строки, набранной вразрядку.

    Запятую подтягиваем к слову: отодвинутая на общий шаг, она читается
    апострофом («Суббота' 5 сентября»).
    """
    steps = []
    for i, ch in enumerate(s):
        step = draw.textlength(ch, font=f)
        nxt = s[i + 1] if i + 1 < len(s) else ""
        if nxt in ",.!?":
            step -= tracking / 4
        elif nxt:
            step += tracking
        steps.append(step)
    return steps


def _tracked_width(draw: ImageDraw.ImageDraw, s: str, f, tracking: int) -> int:
    return int(sum(_advances(draw, s, f, tracking)))


def _draw_tracked(
    draw: ImageDraw.ImageDraw, xy, s: str, f, fill, tracking: int, centered: bool = True
) -> None:
    """Строка вразрядку. Печатаем посимвольно: letter-spacing у Pillow нет."""
    x, y = xy
    if centered:
        x -= _tracked_width(draw, s, f, tracking) / 2
    for ch, step in zip(s, _advances(draw, s, f, tracking)):
        draw.text((x, y), ch, font=f, fill=fill, anchor="lt")
        x += step


def _fit_title(draw: ImageDraw.ImageDraw, s: str, max_width: int):
    """Заголовок и подобранный к нему кегль: (строки, шрифт).

    Сначала пробуем уместить в одну строку, уменьшая кегль с 84 до 44, и
    только потом переносим по словам: «Луна уходит в Рак» должно остаться
    одной крупной строкой.
    """
    sizes = range(84, 43, -2)
    for size in sizes:
        f = _font("PTSerif-Regular.ttf", size)
        if draw.textlength(s, font=f) <= max_width:
            return [s], f

    # Ломаем там, где половины выходят ближе всего по длине.
    words = s.split()
    split = min(
        range(1, len(words)),
        key=lambda i: abs(len(" ".join(words[:i])) - len(" ".join(words[i:]))),
    )
    lines = [" ".join(words[:split]), " ".join(words[split:])]
    for size in sizes:
        f = _font("PTSerif-Regular.ttf", size)
        if max(draw.textlength(ln, font=f) for ln in lines) <= max_width:
            return lines, f
    return lines, _font("PTSerif-Regular.ttf", 44)


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
    """Подпись под заголовком: фаза с процентом, а под лунацией — её время.

    В новолуние и полнолуние фазу уже назвал заголовок, и повтор («Новолуние
    в Деве» / «новолуние») выглядел бы ошибкой.
    """
    phase = moon["phase_ru"]
    if phase not in ("новолуние", "полнолуние"):
        return f"{phase}, {moon['illumination']}%"
    if event is None:
        return phase
    return f"точное в {event['moment']:%H:%M}"


# --- Луна -----------------------------------------------------------------


def _semiaxis(radius: int, illumination: int) -> int:
    """Полуось терминатора: R*|1-2k| по освещённой доле k."""
    return round(radius * abs(1 - 2 * illumination / 100))


def _moon_masks(radius: int, illumination: int, growing: bool) -> tuple[Image.Image, Image.Image]:
    """Маски освещённой и тёмной части диска.

    Терминатор — полуэллипс с полуосью R*|1-2k| по освещённой доле k: до
    половины он выгнут в сторону света (серп), после — от него (горб).
    Растущая Луна освещена справа, убывающая слева (северное полушарие).
    """
    d = radius * 2
    k = illumination / 100

    circle = Image.new("L", (d, d), 0)
    ImageDraw.Draw(circle).ellipse((0, 0, d - 1, d - 1), fill=255)

    half = Image.new("L", (d, d), 0)
    lit_side = (radius, 0, d - 1, d - 1) if growing else (0, 0, radius - 1, d - 1)
    ImageDraw.Draw(half).rectangle(lit_side, fill=255)
    lit_half = ImageChops.multiply(circle, half)

    a = _semiaxis(radius, illumination)
    ell = Image.new("L", (d, d), 0)
    if a > 0:
        ImageDraw.Draw(ell).ellipse((radius - a, 0, radius + a - 1, d - 1), fill=255)
    ell = ImageChops.multiply(ell, circle)

    if k <= 0.5:
        lit = ImageChops.subtract(lit_half, ell)
    else:
        dark_half = ImageChops.subtract(circle, lit_half)
        lit = ImageChops.add(lit_half, ImageChops.multiply(ell, dark_half))
    return lit, ImageChops.subtract(circle, lit)


def _draw_moon(img: Image.Image, center: tuple[int, int], radius: int, moon: dict) -> None:
    """Диск Луны: контур, штриховка тени, дуга терминатора."""
    d = radius * 2
    left, top = center[0] - radius, center[1] - radius
    lit, dark = _moon_masks(radius, moon["illumination"], moon["growing"])

    img.paste(Image.new("RGB", (d, d), LIT_FILL), (left, top), lit)

    # Штрихуем весь квадрат и обрезаем маской тени — рисовать линии по форме
    # серпа пришлось бы считая пересечения с эллипсом.
    hatch = Image.new("RGB", (d, d), GOLD_DIM)
    hatch_mask = Image.new("L", (d, d), 0)
    hm = ImageDraw.Draw(hatch_mask)
    for x in range(0, d, 7):
        hm.line((x, 0, x, d), fill=255, width=1)
    img.paste(hatch, (left, top), ImageChops.multiply(hatch_mask, dark))

    draw = ImageDraw.Draw(img)
    draw.ellipse((left, top, left + d - 1, top + d - 1), outline=GOLD, width=2)

    # У эллипса-терминатора рисуем одну половину: граница света и тени одна,
    # вторая дуга превратила бы её в «линзу» посреди диска.
    a = _semiaxis(radius, moon["illumination"])
    if a > 1:
        term = Image.new("RGBA", (d, d), (0, 0, 0, 0))
        ImageDraw.Draw(term).ellipse(
            (radius - a, 1, radius + a - 1, d - 2), outline=GOLD + (255,), width=2
        )
        side = Image.new("L", (d, d), 0)
        keep_right = moon["growing"] == (moon["illumination"] <= 50)
        box = (radius, 0, d - 1, d - 1) if keep_right else (0, 0, radius - 1, d - 1)
        ImageDraw.Draw(side).rectangle(box, fill=255)
        img.paste(term, (left, top), ImageChops.multiply(term.getchannel("A"), side))


def _draw_sparks(img: Image.Image, day: date) -> None:
    """Крестики по фону.

    Раскладываем по сетке со случайным сдвигом в ячейке: чистый random
    сбивает искорки в кучу и оставляет половину карточки пустой. Зерно —
    номер дня, чтобы перерисовка давала ту же картинку, а не новую россыпь.
    """
    rnd = random.Random(day.toordinal())
    draw = ImageDraw.Draw(img, "RGBA")
    cells = 7
    step = (SIZE - 160) // cells
    for row in range(cells):
        for col in range(cells):
            if rnd.random() > 0.8:
                continue
            x = 80 + col * step + rnd.randint(8, step - 8)
            y = 80 + row * step + rnd.randint(8, step - 8)
            if any(x0 < x < x1 and y0 < y < y1 for x0, y0, x1, y1 in _BUSY):
                continue
            size = rnd.choice((3, 4, 6, 8))
            color = GOLD + (rnd.randint(70, 160),)
            if size <= 4:
                draw.ellipse((x, y, x + 2, y + 2), fill=color)
            else:
                draw.line((x - size, y, x + size, y), fill=color, width=1)
                draw.line((x, y - size, x, y + size), fill=color, width=1)


# --- сборка ---------------------------------------------------------------


def build(day: date, moon: dict, event: dict | None) -> Image.Image:
    """Карточка целиком. moon и event — из day_sky() и main_event()."""
    img = Image.new("RGB", (SIZE, SIZE), BG)
    _draw_sparks(img, day)
    draw = ImageDraw.Draw(img)

    draw.rectangle((44, 44, SIZE - 45, SIZE - 45), outline=GOLD_DIM, width=2)

    date_line = f"{_WEEKDAYS_RU[day.weekday()]}, {day.day} {_MONTHS_RU[day.month - 1]}"
    _draw_tracked(draw, (SIZE // 2, 152), date_line, _font("PTSerif-Bold.ttf", 30), GOLD, 9)

    _draw_moon(img, (SIZE // 2, 362), 100, moon)

    lines, title_font = _fit_title(draw, headline(event, moon), SIZE - 260)
    y = 552 if len(lines) == 1 else 512
    for line in lines:
        draw.text((SIZE // 2, y), line, font=title_font, fill=CREAM, anchor="mt")
        y += int(title_font.size * 1.18)

    draw.text(
        (SIZE // 2, y + 14), subtitle(moon, event),
        font=_font("PTSerif-Italic.ttf", 42), fill=GOLD_LIGHT, anchor="mt",
    )

    # Подвал: полумесяц вырезаем двумя кругами — цветные эмодзи Pillow не рисует.
    logo = _font("PTSerif-Bold.ttf", 26)
    label = "ЗВЕЗДОЧЁТ"
    x0 = (SIZE - (62 + _tracked_width(draw, label, logo, 11))) // 2
    crescent = Image.new("RGBA", (34, 34), (0, 0, 0, 0))
    cd = ImageDraw.Draw(crescent)
    cd.ellipse((2, 2, 31, 31), fill=GOLD + (255,))
    cd.ellipse((-6, 2, 23, 31), fill=(0, 0, 0, 0))
    img.paste(crescent, (x0, 906), crescent)
    _draw_tracked(draw, (x0 + 62, 912), label, logo, GOLD, 11, centered=False)

    return img


def render(day: date) -> bytes:
    """PNG карточки этого дня. Небо считает сама — 20 мс, кеш не нужен."""
    sky = transits.day_sky(day)
    img = build(day, sky["moon"], main_event(sky))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
