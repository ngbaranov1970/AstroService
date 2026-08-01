"""Выборки для админки стенда: что сгенерировано и как это оценили.

Отдельный модуль, а не функции в charts.py/reports.py, по границе назначения:
там продуктовые вызовы, которые позже дёрнут бот и Mini App, здесь —
инструмент для двух человек, который вместе со стендом и умрёт. Смешивать
их значит тащить админские джойны в бота.

Единица работы — ОТЧЁТ, а не карта: у одной карты уже два текста
(free_natal + full_natal), а после бампа VERSION в промпте появится третий,
и сравнивать надо именно тексты. Персональные данные (имя, дата, город)
здесь есть — страница закрыта HTTP Basic, см. app/web/deps.require_admin.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import BigInteger, Float, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chart, Event, Report, ReportType, User
from app.services import events


@dataclass(frozen=True, slots=True)
class ReportRow:
    """Одна строка таблицы `/admin`: текст плюс контекст, чей он.

    Полей больше, чем колонок в таблице: `chart_id` нужен, чтобы собрать
    рядом все редакции одной карты, а `person_name` бывает пустым — тогда
    человека различают по дате и городу.
    """

    report_id: int
    chart_id: int
    chart_token: str
    user_id: int
    is_tester: bool
    person_name: str | None
    birth_local: datetime
    time_known: bool
    place_name: str
    type: ReportType
    prompt_version: str
    model: str
    temperature: float
    chars: int
    tokens_completion: int
    created_at: datetime
    rating: float | None
    ratings_count: int


def _ratings_subquery():
    """Средняя оценка и число оценок по каждому отчёту.

    Ключ — `props->>'report_id'`, а не пара (карта, тип): оценивают
    конкретную редакцию текста, и после смены VERSION старые звёзды не
    должны переезжать на новый разбор. Оценки, записанные до того, как
    router стал класть `report_id` в props, сюда не попадают — их единицы,
    и восстанавливать их по (chart_id, type) значит приписывать оценку
    тексту, которого человек не видел.
    """
    report_id = cast(Event.props["report_id"].astext, BigInteger)
    return (
        select(
            report_id.label("report_id"),
            func.avg(cast(Event.props["rating"].astext, Float)).label("rating"),
            func.count().label("ratings_count"),
        )
        .where(
            Event.name == events.FEEDBACK,
            Event.props["report_id"].astext.is_not(None),
        )
        .group_by(report_id)
        .subquery()
    )


async def list_report_rows(session: AsyncSession, limit: int = 200) -> list[ReportRow]:
    """Все сгенерированные тексты, свежие первыми.

    Сам текст в выборку НЕ идёт — только его длина: на странице списка он не
    показывается, а двести полных разборов это мегабайты, прочитанные зря.
    Длина при этом нужна глазами: промпт просит 350–500 слов, и раздувшийся
    ответ видно прямо в таблице.
    """
    ratings = _ratings_subquery()

    stmt = (
        select(
            Report.id,
            Report.chart_id,
            Chart.token.label("chart_token"),
            Report.user_id,
            User.is_tester,
            Chart.person_name,
            Chart.birth_local,
            Chart.time_known,
            Chart.place_name,
            Report.type,
            Report.prompt_version,
            Report.model,
            Report.temperature,
            func.length(Report.text).label("chars"),
            Report.tokens_completion,
            Report.created_at,
            ratings.c.rating,
            ratings.c.ratings_count,
        )
        .join(Chart, Chart.id == Report.chart_id)
        .join(User, User.id == Report.user_id)
        .outerjoin(ratings, ratings.c.report_id == Report.id)
        .order_by(Report.created_at.desc())
        .limit(limit)
    )

    return [
        ReportRow(
            report_id=row.id,
            chart_id=row.chart_id,
            chart_token=row.chart_token,
            user_id=row.user_id,
            is_tester=row.is_tester,
            person_name=row.person_name,
            birth_local=row.birth_local,
            time_known=row.time_known,
            place_name=row.place_name,
            type=row.type,
            prompt_version=row.prompt_version,
            model=row.model,
            temperature=row.temperature,
            chars=row.chars,
            tokens_completion=row.tokens_completion,
            created_at=row.created_at,
            rating=row.rating,
            ratings_count=row.ratings_count or 0,
        )
        for row in await session.execute(stmt)
    ]


@dataclass(frozen=True, slots=True)
class ReportFull:
    """Отчёт целиком вместе со своей картой — для страницы сравнения.

    Карта нужна рядом с текстом не для красоты: главная проверка промпта —
    названы ли за тезисами конкретные элементы карты, а без самой карты
    судить об этом нельзя.
    """

    report: Report
    chart: Chart
    rating: float | None


async def get_reports_for_compare(
    session: AsyncSession, report_ids: list[int]
) -> list[ReportFull]:
    """Отчёты по списку id — в том порядке, в каком их назвали в ссылке.

    Порядок сохраняем сами: `IN (...)` его не гарантирует, а колонки на
    странице обязаны стоять так, как их выбрала помощница. Ненайденные id
    молча пропускаются — ссылку правят руками, и опечатка не повод ронять
    страницу.
    """
    if not report_ids:
        return []

    ratings = _ratings_subquery()

    stmt = (
        select(Report, Chart, ratings.c.rating)
        .join(Chart, Chart.id == Report.chart_id)
        .outerjoin(ratings, ratings.c.report_id == Report.id)
        .where(Report.id.in_(report_ids))
    )

    found = {
        report.id: ReportFull(report=report, chart=chart, rating=rating)
        for report, chart, rating in await session.execute(stmt)
    }
    return [found[rid] for rid in report_ids if rid in found]


@dataclass(frozen=True, slots=True)
class VersionRow:
    """Сводка по одной версии промпта: сколько текстов и как их оценили."""

    type: ReportType
    prompt_version: str
    count: int
    avg_chars: int
    rating: float | None
    ratings_count: int


async def list_versions(session: AsyncSession) -> list[VersionRow]:
    """Итог по каждой версии промпта — ответ на «стало ли лучше после правки».

    Средние по горстке текстов ничего не доказывают статистически, но
    показывают направление и, главное, разброс длины: промпт, который у
    одних даёт 400 слов, а у других 900, надо чинить независимо от оценок.
    """
    ratings = _ratings_subquery()

    stmt = (
        select(
            Report.type,
            Report.prompt_version,
            func.count(Report.id).label("count"),
            func.avg(func.length(Report.text)).label("avg_chars"),
            func.avg(ratings.c.rating).label("rating"),
            func.coalesce(func.sum(ratings.c.ratings_count), 0).label("ratings_count"),
        )
        .outerjoin(ratings, ratings.c.report_id == Report.id)
        .group_by(Report.type, Report.prompt_version)
        .order_by(Report.type, Report.prompt_version)
    )

    return [
        VersionRow(
            type=row.type,
            prompt_version=row.prompt_version,
            count=row.count,
            avg_chars=int(row.avg_chars or 0),
            rating=row.rating,
            ratings_count=int(row.ratings_count or 0),
        )
        for row in await session.execute(stmt)
    ]


async def count_charts(session: AsyncSession) -> int:
    """Сколько карт посчитано всего — для шапки страницы."""
    return await session.scalar(select(func.count()).select_from(Chart)) or 0
