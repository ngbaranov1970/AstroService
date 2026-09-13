"""Анонимная ветка приглашения: экран партнёра, который к нам не заходил.

ЭТОТ ФАЙЛ — ЕДИНСТВЕННЫЙ ВХОД В ПРОДУКТ БЕЗ BEARER. Отдельный файл ради
этого и заведён: «работает без авторизации» должно быть видно по имени,
а не по отсутствию одной зависимости в сигнатуре маршрута.

Ключ доступа здесь — invite_token, он же пароль: в логи и в аналитику не
идёт никогда. На «нет токена», «протух» и «уже использован» отвечаем
одинаковым 404 — различать их значит подтверждать существование токена
тому, кто перебирает.

Наружу отдаём минимум: тип связи, подпись зовущего и, после ввода данных,
бесплатный тизер про эту пару. Ни дат, ни мест рождения, ни токена пары.
"""

from __future__ import annotations

from datetime import datetime, time

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import generation
from app.api.deps import SessionDep
from app.models import Pair, ReportType
from app.schemas.charts import ChartIn
from app.schemas.pairs import InvitePublic
from app.schemas.reports import ReportOut, ReportStatus, SectionOut
from app.services import charts as charts_service
from app.services import events
from app.services import pairs as pairs_service
from app.services import reports as reports_service

router = APIRouter(tags=["invites"])

# Что партнёр видит бесплатно. Платного разбора в анонимной ветке нет и не
# будет: покупает тот, у кого пара в аккаунте.
TEASER = ReportType.PAIR_FREE


@router.get("/invites/{token}", response_model=InvitePublic)
async def read_invite(token: str, session: SessionDep) -> InvitePublic:
    """Кто зовёт и на какой разбор — всё, что видно до ввода данных.

    Коммитит ради одного события: без записи «ссылку открыли» доходимость
    приглашения нечем мерить, а она и есть смысл виральной ветки.
    """
    pair = await pairs_service.get_pair_by_invite(session, token)
    if pair is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Приглашение не найдено")

    # Единственное событие без user_id: личности на этом экране ещё нет.
    # Дедупа тоже нет — already_logged ключуется по user_id, — поэтому
    # перезагрузка страницы даёт лишнюю строку, и в отчётах ступень
    # считается по числу разных pair_id, а не по числу строк.
    await events.log(
        session, events.PAIR_INVITE_OPENED, pair_id=pair.id, kind=pair.kind.value
    )
    await session.commit()

    return InvitePublic(kind=pair.kind, inviter_label=pair.inviter_label)


@router.post(
    "/invites/{token}",
    response_model=ReportOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def accept_invite(
    token: str,
    payload: ChartIn,
    session: SessionDep,
    background: BackgroundTasks,
) -> ReportOut:
    """Партнёр вводит свои данные: пара достраивается, тизер уходит в работу.

    Карта заводится в аккаунте ИНИЦИАТОРА, а не партнёра: своего аккаунта у
    того может не быть вовсе — он пришёл по ссылке и никем не опознан.
    Экран приглашения обязан предупредить об этом прямым текстом (152-ФЗ).

    Отвечает состоянием тизера, а не пустотой: фронту дальше опрашивать
    GET /invites/{token}/report, и первое состояние он получает сразу.
    """
    pair = await pairs_service.get_pair_by_invite(session, token)
    if pair is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Приглашение не найдено")

    # Сборка карты повторяет POST /charts: полночь — заглушка, её заменит
    # полдень внутри create_chart, а таймзону сервер считает по координатам.
    birth_local = datetime.combine(payload.birth_date, payload.birth_time or time.min)
    place = charts_service.place_from_coords(payload.lat, payload.lon, payload.place_name)
    chart_b = await charts_service.create_chart(
        session,
        user_id=pair.user_id,
        birth_local=birth_local,
        place=place,
        time_known=payload.birth_time is not None,
    )
    if payload.person_name and chart_b.person_name != payload.person_name:
        chart_b.person_name = payload.person_name

    try:
        pair = await pairs_service.attach_partner(session, pair, chart_b)
    except ValueError as exc:
        # Партнёр ввёл данные, совпавшие с картой стороны A: пара человека
        # с самим собой. Это ошибка ввода, а не поломка.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Эти данные совпадают с данными пригласившего"
        ) from exc

    await events.log(
        session,
        events.PAIR_PARTNER_JOINED,
        # user_id — инициатора: он владелец пары и обеих карт, а партнёр
        # нам никем не представлен.
        user_id=pair.user_id,
        pair_id=pair.id,
        kind=pair.kind.value,
    )
    await events.log(
        session,
        events.PAIR_CREATED,
        user_id=pair.user_id,
        pair_id=pair.id,
        kind=pair.kind.value,
    )
    # Коммит до постановки в очередь: фоновая задача открывает свою сессию и
    # незакоммиченной стороны B не увидит.
    await session.commit()

    return await _start_teaser(session, pair, background)


@router.get("/invites/{token}/report", response_model=ReportOut)
async def read_teaser(
    token: str, session: SessionDep, background: BackgroundTasks
) -> ReportOut:
    """Состояние бесплатного тизера по принятому приглашению.

    Ключ тот же invite_token: ссылка, которой партнёр вошёл, остаётся его
    единственным входом к тексту про него самого. Живость приглашения здесь
    не проверяется (оно закрылось в момент приёма) — вместо неё условие
    «этой ссылкой действительно вошли и пара собрана».
    """
    pair = await pairs_service.get_pair_by_invite(session, token, open_only=False)
    if pair is None or pair.invite_used_at is None or not pair.is_complete:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Приглашение не найдено")

    return await _start_teaser(session, pair, background)


async def _start_teaser(
    session: AsyncSession, pair: Pair, background: BackgroundTasks
) -> ReportOut:
    """Состояние тизера; если он ещё не начат — ставит генерацию в очередь.

    Запускать умеет и GET, а не только приём приглашения: реестр генераций
    живёт в памяти процесса, и перезапуск между POST и первым опросом иначе
    оставил бы партнёра навсегда в состоянии «available» без текста.

    Упавшую генерацию не перезапускаем намеренно: фронт опрашивает раз в
    несколько секунд, и повтор по failed означал бы новую попытку каждые
    пять секунд — то есть счёт от DeepSeek за один сломанный вход.
    """
    state = await _teaser_state(session, pair)
    if state.status is not ReportStatus.AVAILABLE:
        return state

    generation.schedule_pair(background, pair, TEASER)
    return state.model_copy(update={"status": ReportStatus.RUNNING})


async def _teaser_state(session: AsyncSession, pair: Pair) -> ReportOut:
    """Состояние тизера: готов, готовится, упал или можно просить.

    Своя короткая копия _state из routes/reports.py, а не переиспользование:
    там половина логики про пейволл и цену, а в анонимной ветке платного
    нет по определению — тизер бесплатен, и покупает не тот, кто здесь.
    """
    out = ReportOut(
        type=TEASER,
        title=reports_service.title(TEASER),
        status=ReportStatus.AVAILABLE,
        section_titles=reports_service.section_titles(TEASER),
    )

    report = await reports_service.get_pair_report(session, pair.id, TEASER)
    if report is not None:
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

    gen = generation.pair_state(pair.id, TEASER)
    if gen is not None and gen.running:
        return out.model_copy(update={"status": ReportStatus.RUNNING})
    if gen is not None and gen.error is not None:
        return out.model_copy(
            update={
                "status": ReportStatus.FAILED,
                "error_code": gen.error_code,
                "error": gen.error,
            }
        )

    return out
