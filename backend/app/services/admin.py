"""Выборки для админки: что сгенерировано, как это оценили и где отваливаются.

Отдельный модуль, а не функции в charts.py/reports.py, по границе назначения:
там продуктовые вызовы, которые дёргают Mini App и сайт, здесь — инструмент
для двух человек. Смешивать их значит тащить админские джойны в продукт.

Единиц работы две, и они не сводятся друг к другу. Для текстов это ОТЧЁТ, а
не карта: у одной карты уже два текста (free_natal + full_natal), а после
бампа VERSION появится третий, и сравнивать надо именно тексты. Для воронки
это ЧЕЛОВЕК: один завёл три карты — это по-прежнему один дошедший, и счёт
строк завысил бы шаг.

Персональные данные (имя, дата, город) здесь есть — страница закрыта
HTTP Basic, см. app/web/deps.require_admin.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

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


# --- Воронка ---------------------------------------------------------------
# Считается КОГОРТОЙ: человек попадает в отчёт по дате своего первого
# app_opened, а шаги его засчитываются любой давности. Иначе в «заплатил» за
# неделю рекламы попали бы августовские покупатели и конверсия задралась бы
# вверх. Обратная сторона: когорта последних дней всегда недосчитана —
# пришедший вчера ещё может заплатить завтра.


def _kind(props: dict[str, Any]) -> str | None:
    """Вид разбора из props. Ключ у разных событий разный.

    report_* кладут его в `type` (api/routes/reports.py), order_* — в
    `report_type` (services/orders.py). Свести к одному ключу нельзя:
    строки в базе уже написаны, и переименование обнулило бы историю.
    """
    return props.get("type") or props.get("report_type")


def _of(report_type: ReportType) -> Callable[[dict[str, Any]], bool]:
    """Условие «событие про этот вид разбора»."""
    return lambda props: _kind(props) == report_type.value


def _pair(props: dict[str, Any]) -> bool:
    """Событие про пару. У натальных pair_id либо нет, либо он None."""
    return props.get("pair_id") is not None


def _natal(props: dict[str, Any]) -> bool:
    return props.get("pair_id") is None


@dataclass(frozen=True, slots=True)
class _Step:
    """Ступень: заголовок, имя события и условие на его props."""

    title: str
    name: str
    # Условие — функция, а не кусок SQL. Натальный пейволл отличается от
    # парного только ключом внутри props, и в SQL каждая такая ступень стала
    # бы отдельным подзапросом: пятнадцать подзапросов никто потом не правит.
    match: Callable[[dict[str, Any]], bool] = field(default=lambda props: True)


@dataclass(frozen=True, slots=True)
class _FunnelSpec:
    """Воронка целиком. surface=None — «обе площадки»."""

    title: str
    surface: str | None
    steps: tuple[_Step, ...]


_FUNNELS: tuple[_FunnelSpec, ...] = (
    _FunnelSpec(
        title="VK Mini App",
        surface="vk",
        # Кончается не деньгами: внутри Mini App продавать нельзя, и всё, что
        # приложение может добыть, — право написать человеку ещё раз.
        steps=(
            _Step("Запустил приложение", events.APP_OPENED),
            _Step("Ввёл данные рождения", events.CHART_CREATED),
            _Step("Попросил разбор", events.REPORT_REQUESTED, _of(ReportType.FREE_NATAL)),
            _Step("Получил текст", events.REPORT_READY, _of(ReportType.FREE_NATAL)),
            # Эта ступень пока всегда ноль: group_offer_shown не пишет никто,
            # хотя блок в приложении есть (см. docs/progress.md).
            _Step("Увидел блок сообщества", events.GROUP_OFFER_SHOWN),
            _Step("Разрешил писать", events.GROUP_MESSAGES_ALLOWED),
        ),
    ),
    _FunnelSpec(
        title="Сайт (вход через VK ID)",
        surface="web",
        steps=(
            _Step("Пришёл из браузера", events.APP_OPENED),
            _Step("Ввёл данные рождения", events.CHART_CREATED),
            _Step("Получил бесплатный разбор", events.REPORT_READY, _of(ReportType.FREE_NATAL)),
            _Step("Увидел цену", events.PAYWALL_SHOWN, _natal),
            _Step("Нажал «купить»", events.ORDER_CREATED, _of(ReportType.FULL_NATAL)),
            _Step("Ушёл на оплату", events.PAYMENT_STARTED, _of(ReportType.FULL_NATAL)),
            _Step("Заплатил", events.ORDER_PAID, _of(ReportType.FULL_NATAL)),
        ),
    ),
    _FunnelSpec(
        title="Совместимость",
        # Обе площадки: пару собирают и в VK, и на сайте, а платят только на
        # сайте — разрыв между «увидел цену» и «нажал купить» здесь штатный.
        surface=None,
        steps=(
            _Step("Собрал пару", events.PAIR_CREATED),
            _Step("Увидел цену пары", events.PAYWALL_SHOWN, _pair),
            _Step("Нажал «купить»", events.ORDER_CREATED, _of(ReportType.PAIR_FULL)),
            _Step("Заплатил", events.ORDER_PAID, _of(ReportType.PAIR_FULL)),
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class FunnelStep:
    """Одна строка таблицы воронки: сколько людей дошло и какой ценой."""

    title: str
    users: int
    # None — «делить не на что»: предыдущая ступень пуста. Отличать от 0.0
    # обязательно, иначе непишущееся событие выглядит нулевой конверсией
    # следующего за ним шага, а не собственным пробелом в данных.
    of_first: float | None
    of_prev: float | None


@dataclass(frozen=True, slots=True)
class Funnel:
    title: str
    steps: list[FunnelStep]


def _surface(platform: str | None) -> str:
    """Площадка первого захода: "web" или "vk".

    Сравнение с "web" точное. Это наша метка из web/routes/vkid.py, а VK в
    launch params присылает mobile_android, mobile_iphone, mobile_web и
    desktop_web — проверка «содержит web» утащила бы половину VK в веб.
    По users.platform разделить нельзя вовсе: VK ID отдаёт тот же vk_user_id,
    и обе площадки лежат в базе как platform='vk'.
    """
    return "web" if platform == "web" else "vk"


def _pct(part: int, whole: int) -> float | None:
    """Доля в процентах. Пустой знаменатель даёт None — «делить не на что»."""
    return round(100 * part / whole, 1) if whole else None


def _assemble(
    spec: _FunnelSpec,
    surface_of: dict[int, str],
    hits: list[tuple[str, int, dict[str, Any]]],
) -> Funnel:
    """Собрать одну воронку из готовой когорты и её событий."""
    members = {
        user_id
        for user_id, surface in surface_of.items()
        if spec.surface is None or surface == spec.surface
    }

    steps: list[FunnelStep] = []
    first = previous = 0
    for step in spec.steps:
        reached = len(
            {
                user_id
                for name, user_id, props in hits
                if name == step.name and user_id in members and step.match(props)
            }
        )
        if not steps:
            first = reached
        steps.append(
            FunnelStep(
                title=step.title,
                users=reached,
                of_first=_pct(reached, first),
                of_prev=_pct(reached, previous) if steps else 100.0,
            )
        )
        previous = reached

    return Funnel(title=spec.title, steps=steps)


async def build_funnels(
    session: AsyncSession,
    *,
    since: datetime,
    until: datetime,
    include_testers: bool = False,
) -> list[Funnel]:
    """Три воронки (VK, сайт, пара) по когорте пришедших за период.

    Когорта — те, чей ПЕРВЫЙ app_opened попал в [since, until); шаги
    засчитываются без ограничения по дате, иначе открывший 21-го и
    заплативший 25-го потерялся бы. Считаются люди, а не события.

    Тестеры по умолчанию исключены: они ходят по продукту постоянно и в
    когорте рекламы искажают верх воронки.
    """
    # DISTINCT ON — постгресовый способ взять первую строку группы целиком.
    # MIN(ts) дал бы только время, а нужна площадка именно первого захода:
    # она лежит в props того же события.
    first_open = (
        select(
            Event.user_id.label("user_id"),
            Event.ts.label("first_ts"),
            Event.props["platform"].astext.label("platform"),
        )
        .where(Event.name == events.APP_OPENED, Event.user_id.is_not(None))
        .order_by(Event.user_id, Event.ts)
        .distinct(Event.user_id)
        .subquery()
    )

    cohort_stmt = (
        select(first_open.c.user_id, first_open.c.platform)
        .select_from(first_open)
        .join(User, User.id == first_open.c.user_id)
        .where(first_open.c.first_ts >= since, first_open.c.first_ts < until)
    )
    if not include_testers:
        cohort_stmt = cohort_stmt.where(User.is_tester.is_(False))

    surface_of = {
        row.user_id: _surface(row.platform)
        for row in await session.execute(cohort_stmt)
    }
    if not surface_of:
        # in_([]) отработал бы корректно, но это лишний поход в базу за
        # заведомо пустым ответом.
        return [_assemble(spec, {}, []) for spec in _FUNNELS]

    # Все события когорты одним запросом, раскладка по ступеням — в Python.
    # При когорте в сотни человек это тысячи строк и полтора десятка проходов
    # по ним. Переписывать в агрегат стоит, когда событий станут десятки
    # тысяч; до тех пор читаемость дороже.
    names = {step.name for spec in _FUNNELS for step in spec.steps}
    hits = [
        (row.name, row.user_id, row.props)
        for row in await session.execute(
            select(Event.name, Event.user_id, Event.props).where(
                Event.user_id.in_(list(surface_of)), Event.name.in_(names)
            )
        )
    ]

    return [_assemble(spec, surface_of, hits) for spec in _FUNNELS]


async def count_events(
    session: AsyncSession,
    *,
    since: datetime,
    until: datetime,
    names: list[str],
) -> dict[str, int]:
    """Сколько раз случилось каждое из названных событий за период.

    Счёт СОБЫТИЙ и без когорты — для того, что воронкой не меряется:
    доходимость ссылок-приглашений (у pair_invite_opened user_id всегда NULL,
    экран анонимный), аварии и отказы генерации, охват рассылки. Имена, по
    которым ничего не нашлось, в ответе отсутствуют.
    """
    stmt = (
        select(Event.name, func.count())
        .where(Event.ts >= since, Event.ts < until, Event.name.in_(names))
        .group_by(Event.name)
    )
    return {name: count for name, count in await session.execute(stmt)}
