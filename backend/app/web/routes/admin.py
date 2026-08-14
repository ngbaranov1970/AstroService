"""Маршруты админки стенда: что сгенерировано и чем один текст лучше другого.

Отдельный роутер, а не секция в router.py: `require_admin` висит на всём
APIRouter сразу, поэтому забыть охрану на новом маршруте невозможно — а за
страницами имена, даты и места рождения живых людей. Выборки живут в
app/services/admin.py, здесь только разбор параметров и шаблон.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from app.db import get_session
from app.services import admin as admin_service
from app.services import reports
from app.web.deps import require_admin
from app.web.templating import templates

router = APIRouter(
    prefix="/admin",
    # Зависимость в dependencies, а не в аргументах хендлеров: имя вошедшего
    # нам не нужно, нужен сам факт проверки — и он теперь общий для префикса.
    dependencies=[Depends(require_admin)],
    include_in_schema=False,
)

SessionDep = Annotated[AsyncSession, Depends(get_session)]

# Потолок строк в таблице: двести отчётов — это уже месяцы обкатки промптов,
# а страница остаётся одним экраном HTML без пагинации.
LIST_LIMIT = 200

# Сколько текстов помещается рядом, чтобы колонка осталась читаемой.
MAX_COMPARE = 4


@router.get("")
async def index(request: Request, session: SessionDep) -> Response:
    """Таблица всех разборов плюс сводка по версиям промптов."""
    return templates.TemplateResponse(
        request,
        "admin.html",
        {
            "rows": await admin_service.list_report_rows(session, limit=LIST_LIMIT),
            "versions": await admin_service.list_versions(session),
            "charts_count": await admin_service.count_charts(session),
            "limit": LIST_LIMIT,
        },
    )


@router.get("/compare")
async def compare(
    request: Request,
    session: SessionDep,
    ids: Annotated[list[str] | None, Query()] = None,
) -> Response:
    """Тексты выбранных разборов рядом колонками.

    Оба написания `ids` понимаются одинаково: `?ids=12&ids=15` шлёт форма с
    чекбоксами, `?ids=12,15` удобнее править руками. Мусор в параметре молча
    отбрасывается — ошибка ввода не должна ронять страницу.
    """
    report_ids = _parse_ids(ids)
    items = await admin_service.get_reports_for_compare(session, report_ids)

    return templates.TemplateResponse(
        request,
        "admin_compare.html",
        {
            "items": [
                # Секции считаем здесь: шаблон не должен знать, что full_natal
                # приходит JSON'ом, а free_natal — сплошным текстом.
                {"item": item, "sections": reports.sections(item.report)}
                for item in items
            ],
            "missing": len(report_ids) - len(items),
        },
    )


def _parse_ids(raw: list[str] | None) -> list[int]:
    """[«12,15», «7»] → [12, 15, 7]; нечисловое и повторы выброшены.

    Порядок первого появления сохраняется — он и задаёт порядок колонок,
    а сервис его не меняет.
    """
    seen: dict[int, None] = {}
    for chunk in raw or []:
        for part in chunk.split(","):
            part = part.strip()
            if part.isdigit():
                seen.setdefault(int(part), None)
    return list(seen)[:MAX_COMPARE]
