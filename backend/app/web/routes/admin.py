"""Маршруты админки стенда: что сгенерировано, чем один текст лучше другого
и очередь постов сообщества.

Отдельный роутер, а не секция в router.py: `require_admin` висит на всём
APIRouter сразу, поэтому забыть охрану на новом маршруте невозможно — а за
страницами имена, даты и места рождения живых людей. Выборки живут в
app/services/admin.py и app/services/content.py, здесь только разбор
параметров и шаблон.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from app import generation
from app.db import get_session
from app.services import admin as admin_service
from app.services import content, reports
from app.web.deps import require_admin
from app.web.templating import templates

logger = logging.getLogger(__name__)

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

# Период автообновления страницы контента, пока идёт прогон. Реже, чем 5 с у
# страницы разбора: там ждут минуту, здесь — минут десять, и лишние сотни
# перезагрузок с полным SELECT пачки ни к чему.
CONTENT_REFRESH_SECONDS = 15

# Горизонт пачки: те же 7 дней, что генерирует прогон. Меньше — на экране
# не видно всей недели, больше — пусто, дальше ничего не сгенерировано.
CONTENT_DAYS = 7


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


@router.get("/content")
async def content_page(request: Request, session: SessionDep) -> Response:
    """Пачка постов на неделю и кнопка прогона.

    Только читает: прогон длится минут десять и держал бы ответ всё это
    время — тем же порядком, что на странице разбора.
    """
    run = generation.content_state()
    posts = await content.list_week(session, days=CONTENT_DAYS)

    return templates.TemplateResponse(
        request,
        "admin_content.html",
        {
            "items": [
                # Текст готовим здесь: шаблон не должен знать, что ответ
                # модели лежит в базе JSON'ом и разбирает его модуль промпта.
                # Длина в словах — для калибровки промпта (рамки заданы в его
                # SYSTEM), поэтому она рядом с текстом, а не внутри него.
                {"post": post, "text": text, "words": len(text.split())}
                for post, text in ((p, content.render(p)) for p in posts)
            ],
            "run": run,
            "start": content.today_msk(),
            "days": CONTENT_DAYS,
            # Пока прогон идёт, страница перечитывает себя сама; как только
            # он кончился — перестаёт, иначе экран сам себе создаёт поток
            # запросов на всю ночь.
            "refresh": CONTENT_REFRESH_SECONDS if run is not None and run.running else None,
        },
    )


@router.post("/content/generate")
async def content_generate(background: BackgroundTasks) -> Response:
    """Кнопка «сгенерировать неделю»: ставит прогон в очередь и уводит обратно.

    Отказ замка (прогон уже идёт) отдельно не показываем: страница и так
    напишет, что прогон идёт с такого-то времени.
    """
    if not generation.schedule_content(background):
        logger.info("Прогон контента уже идёт — второй запуск отклонён")

    # 303, а не 302: браузер обязан перейти GET'ом, иначе «обновить» на
    # странице переотправит форму и попробует запустить прогон снова.
    # Токен сессии не дописываем — в админку из VK не ходят, там Basic.
    return RedirectResponse("/admin/content", status_code=status.HTTP_303_SEE_OTHER)


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
