"""JSON-маршруты разборов: состояние текстов по карте и запрос генерации.

Слой тонкий: принять запрос, собрать состояние из сервисов, отдать схему.
Ту же картину рисует стенд (`_report_block` в app/web/routes/stand.py) — он
и этот файл обязаны отвечать одинаково, потому что за ними одни сервисы.

Форма продиктована временем ответа LLM: текст готовится 30–60 секунд, и
маршрута «дай разбор» быть не может — он держал бы соединение минуту.
Вместо этого POST ставит работу в очередь и сразу отвечает, а GET фронт
опрашивает раз в несколько секунд, пока `status` не станет `ready`.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import generation
from app.api.deps import ChartByToken, CurrentUser, SessionDep
from app.models import Chart, ReportType, User
from app.schemas.reports import ReportOut, ReportRequest, ReportStatus, SectionOut
from app.services import events, orders
from app.services import reports as reports_service

router = APIRouter(tags=["reports"])

# Состояния, при которых повторная просьба сгенерировать ничего не меняет:
# текст уже есть, уже готовится или закрыт пейволлом.
_NO_RETRY = frozenset(
    {ReportStatus.READY, ReportStatus.RUNNING, ReportStatus.LOCKED}
)


@router.get("/charts/{token}/reports", response_model=list[ReportOut])
async def list_reports(
    chart: ChartByToken, user: CurrentUser, session: SessionDep
) -> list[ReportOut]:
    """Все виды разборов этой карты с текущим состоянием каждого.

    Отдаются и те, которых ещё нет: фронту нужен весь экран сразу — что
    готово, что готовится, что предлагается купить. Виды берутся из
    supported_types(), заготовки без промпта наружу не уходят.

    Текст видит любой, кто знает ссылку (её для того и пересылают), а вот
    цена считается от пришедшего: покупка привязана к человеку и карте.
    """
    states = [
        await _state(session, chart, report_type, user)
        for report_type in reports_service.supported_types()
    ]

    # Показ цены — первая ступень воронки покупки. Пишем один раз на пару
    # человек+карта: фронт опрашивает этот маршрут, пока идёт генерация, и
    # без проверки одно ожидание давало бы десяток «показов цены».
    locked = any(state.status is ReportStatus.LOCKED for state in states)
    if locked and not await events.already_logged(
        session, events.PAYWALL_SHOWN, user_id=user.id, chart_id=chart.id
    ):
        await events.log(
            session, events.PAYWALL_SHOWN, user_id=user.id, chart_id=chart.id
        )
        await session.commit()

    return states


@router.post("/charts/{token}/reports", response_model=ReportOut)
async def request_report(
    payload: ReportRequest,
    chart: ChartByToken,
    user: CurrentUser,
    session: SessionDep,
    background: BackgroundTasks,
) -> ReportOut:
    """Попросить разбор. Отвечает состоянием, генерация идёт в фоне.

    Ответ всегда одной формы (ReportOut), а не 402 на пейволл: фронту проще
    разобрать одно состояние, чем два разных ответа, и на `locked` в нём уже
    лежат цена и адрес оплаты. Отказ по лимиту или дневному потолку сюда не
    попадает — он выясняется внутри генерации и придёт следующим GET как
    `failed` с кодом причины.

    Повторный запрос безопасен: готовый текст и уже идущая работа возвращают
    себя, не тратя ни лимита, ни денег.
    """
    if chart.user_id != user.id:
        # 403, а не тихое согласие: посторонний с ссылкой не должен тратить
        # лимиты и деньги владельца карты.
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Это чужая карта")

    if payload.type not in reports_service.supported_types():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Такой разбор пока не делаем")

    state = await _state(session, chart, payload.type, user)
    if state.status in _NO_RETRY:
        return state

    # «Попросил» пишем ДО запуска: разница с report_ready и есть доля неудач,
    # которую иначе не увидеть.
    await events.log(
        session,
        events.REPORT_REQUESTED,
        user_id=user.id,
        chart_id=chart.id,
        type=payload.type.value,
    )
    await session.commit()

    generation.schedule(background, chart, payload.type)
    # Ошибку прошлой попытки затираем: очередь уже с новой работой, и фронт
    # должен показать ожидание, а не вчерашний отказ.
    return state.model_copy(
        update={
            "status": ReportStatus.RUNNING,
            "error": None,
            "error_code": None,
        }
    )


async def _state(
    session: AsyncSession, chart: Chart, report_type: ReportType, user: User
) -> ReportOut:
    """Состояние одного разбора: текст, очередь и право на покупку в одном ответе.

    Порядок проверок и есть смысл функции — состояния взаимоисключающие, а
    источников у них три (база, реестр генераций, заказы).
    """
    out = ReportOut(
        type=report_type,
        title=reports_service.title(report_type),
        status=ReportStatus.AVAILABLE,
        section_titles=reports_service.section_titles(report_type),
    )

    report = await reports_service.get_report(session, chart.id, report_type)
    if report is not None:
        # Текст есть — остальное неважно: написанное открыто всегда, в том
        # числе тому, кому переслали ссылку.
        return out.model_copy(
            update={
                "status": ReportStatus.READY,
                "sections": [
                    SectionOut.model_validate(section)
                    for section in reports_service.sections(report)
                ],
                "created_at": report.created_at,
            }
        )

    gen = generation.state(chart.id, report_type)
    if gen is not None and gen.running:
        return out.model_copy(update={"status": ReportStatus.RUNNING})

    if await reports_service.needs_purchase(
        session, user=user, chart=chart, report_type=report_type
    ):
        # Пейволл идёт впереди упавшей генерации: отказ по этой же причине
        # (needs_purchase) без цены и ссылки на оплату — тупик для фронта.
        return out.model_copy(
            update={
                "status": ReportStatus.LOCKED,
                "price_rub": (
                    orders.price_rub(report_type)
                    if orders.is_for_sale(report_type)
                    else None
                ),
                "pay_url": orders.payment_url(chart.token, report_type),
            }
        )

    if gen is not None and gen.error is not None:
        return out.model_copy(
            update={
                "status": ReportStatus.FAILED,
                "error_code": gen.error_code,
                "error": gen.error,
            }
        )

    return out
