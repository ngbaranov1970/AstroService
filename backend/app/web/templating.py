"""Шаблонизатор веб-слоя: один объект Jinja2 и общие для всех страниц глобалы.

Отдельный модуль, потому что шаблоны рисуют все четыре роутера из
app/web/routes/. Пока объект жил в роутере стенда, соседи импортировали
стенд ради одной переменной — и любой их импорт тянул за собой все его
маршруты.

Глобалы страниц регистрируют сами роутеры при импорте: реквизиты и цену
кладёт routes/legal.py, потому что это его данные.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates
from jinja2 import pass_context
from markupsafe import Markup, escape

from app.config import get_settings
from app.web.deps import in_vk, with_session

# Время в базе хранится в UTC, а сверять его человек будет с личным кабинетом
# ЮKassa и выпиской банка — там московское.
MOSCOW = ZoneInfo("Europe/Moscow")

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

# Один объект на процесс: внутри него кеш скомпилированных шаблонов.
# Автоэкранирование .html включено по умолчанию — имя и город из формы
# попадают в страницу безопасно.
templates = Jinja2Templates(directory=TEMPLATES_DIR)


@pass_context
def _url(context, path: str) -> str:
    """Jinja-глобал `url()`: внутренний адрес с токеном сессии, если он есть.

    Глобал, а не переменная контекста: переменную забыли бы передать в один
    из шаблонов, и в VK ровно эта страница стала бы тупиком. `pass_context`
    даёт доступ к `request`, который Jinja2Templates кладёт в контекст сам.
    """
    return with_session(context["request"], path)


@pass_context
def _in_vk(context) -> bool:
    """Jinja-глобал `in_vk()`: рисуем страницу внутри VK или нет.

    Глобал по той же причине, что и `url()`: переменную контекста забудут
    передать в один шаблон из десяти, и в VK именно там останется цена.
    """
    return in_vk(context["request"])


def _vk_group_id() -> int | None:
    """Jinja-глобал `vk_group_id()`: ID сообщества или None, если не задан.

    Функция, а не значение: `get_settings()` при импорте модуля прочитала бы
    `.env` раньше, чем это сделает точка входа, и pytest с alembic получили
    бы требование заполненного окружения.
    """
    return get_settings().vk_group_id


# Жирный markdown: две звёздочки, текст, две звёздочки. DOTALL — модель
# иногда переносит строку внутри выделения.
_BOLD = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)


def md(value: str) -> Markup:
    """Текст от модели → HTML, где `**жирный**` стал <strong>.

    Разметку из значения по-прежнему экранируем — фильтр не отменяет защиту,
    а добавляет ровно одну конструкцию поверх неё.

    Заголовки `##` сюда не доходят: их снимают parse() модулей llm/prompts/.
    """
    # escape() отдаёт Markup, а re.sub на нём вернул бы обычную строку и
    # уронил бы пометку «безопасно» — поэтому сначала str, Markup в конце.
    text = _BOLD.sub(r"<strong>\1</strong>", str(escape(value)))
    return Markup(text)


def _msk(value: datetime | None, fmt: str = "%d.%m.%Y, %H:%M") -> str:
    """Момент времени в московской зоне; None → пустая строка."""
    if value is None:
        return ""
    return value.astimezone(MOSCOW).strftime(fmt)


templates.env.globals["url"] = _url
templates.env.globals["in_vk"] = _in_vk
templates.env.globals["vk_group_id"] = _vk_group_id
templates.env.filters["msk"] = _msk
templates.env.filters["md"] = md
