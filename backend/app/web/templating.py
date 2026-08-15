"""Шаблонизатор веб-слоя: один объект Jinja2 и общие для всех страниц глобалы.

Отдельный модуль, потому что шаблоны рисуют все четыре роутера из
app/web/routes/. Пока объект жил в роутере стенда, соседи импортировали
стенд ради одной переменной — и любой их импорт тянул за собой все его
маршруты.

Глобалы страниц регистрируют сами роутеры при импорте: реквизиты и цену
кладёт routes/legal.py, потому что это его данные.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates
from jinja2 import pass_context

from app.web.deps import with_session

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


def _msk(value: datetime | None, fmt: str = "%d.%m.%Y, %H:%M") -> str:
    """Момент времени в московской зоне; None → пустая строка."""
    if value is None:
        return ""
    return value.astimezone(MOSCOW).strftime(fmt)


templates.env.globals["url"] = _url
templates.env.filters["msk"] = _msk
