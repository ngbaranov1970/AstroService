"""JSON-маршруты слоя пар: сборка пары, чтение и приглашение партнёра.

Слой тонкий по замыслу: принять схему, позвать app/services/pairs.py,
отдать схему. Ни расчёта синастрии, ни SQL здесь нет.

Разборов совместимости тут нет намеренно — они живут в
app/api/routes/reports.py рядом со своими натальными близнецами, потому
что состояние текста собирается для карты и для пары одинаково.

Анонимной ветки здесь тоже нет: экран партнёра открывается без токена, и
это вынесено в отдельный файл routes/invites.py — «работает без Bearer»
должно быть видно по имени файла, а не по отсутствию одной зависимости.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, PairByToken, SessionDep
from app.models import Chart, Pair
from app.schemas.pairs import InviteOut, PairDetail, PairIn, PairOut
from app.services import charts as charts_service
from app.services import events
from app.services import pairs as pairs_service

router = APIRouter(tags=["pairs"])


@router.get("/pairs", response_model=list[PairOut])
async def list_pairs(user: CurrentUser, session: SessionDep) -> list[PairOut]:
    """Пары этого пользователя, свежие первыми.

    Ждущие партнёра тоже здесь: на экране они и есть «приглашение
    отправлено», и без них человек решит, что нажатие пропало.
    """
    rows = await pairs_service.list_pairs(session, user.id)
    return [_out(pair) for pair in rows]


@router.post("/pairs", response_model=PairOut, status_code=status.HTTP_201_CREATED)
async def create_pair(
    payload: PairIn, user: CurrentUser, session: SessionDep
) -> PairOut:
    """Собрать пару из двух карт или завести пару, ждущую партнёра.

    Без `chart_b_token` создаётся незавершённая пара — следующим шагом
    фронт просит для неё приглашение. Повтор того же состава отдаёт уже
    собранную пару, а не вторую такую же.

    Коммитит сам: пару и событие иначе откатит закрытие сессии.
    """
    chart_a = await charts_service.get_chart_by_token(session, payload.chart_a_token)
    if chart_a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Первой карты нет")

    chart_b = None
    if payload.chart_b_token is not None:
        chart_b = await charts_service.get_chart_by_token(session, payload.chart_b_token)
        if chart_b is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Второй карты нет")

    try:
        pair = await pairs_service.create_pair(
            session,
            user_id=user.id,
            chart_a=chart_a,
            chart_b=chart_b,
            kind=payload.kind,
            inviter_label=payload.inviter_label,
        )
    except ValueError as exc:
        # Сервис бросает ValueError на чужую карту и на пару карты с самой
        # собой. Это ошибка запроса, а не поломка, и 400 здесь честнее 403:
        # состав пары фронт собирает сам и может его починить.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Так пару не собрать") from exc

    # PAIR_CREATED пишем только у собранной пары: событие меряет виральную
    # воронку, и незавершённая пара в ней — ещё не результат, а приглашение.
    if pair.is_complete:
        await events.log(
            session,
            events.PAIR_CREATED,
            user_id=user.id,
            pair_id=pair.id,
            kind=pair.kind.value,
        )
    await session.commit()

    # Карты после commit берём из тех, что уже в руках: связи объявлены
    # lazy="raise_on_sql", а перезапрашивать пару ради двух имён незачем.
    return _out(pair, chart_a=chart_a, chart_b=chart_b)


@router.get("/pairs/{token}", response_model=PairDetail)
async def get_pair(pair: PairByToken) -> PairDetail:
    """Пара вместе с расчётом синастрии — всё, что нужно экрану пары.

    Владельца проверяет сама зависимость PairByToken, поэтому аргумента под
    пользователя здесь нет. У пары, ждущей партнёра, `calc` приходит `null`.
    """
    return PairDetail(**_out(pair).model_dump(), calc=pair.calc)


@router.post("/pairs/{token}/invite", response_model=InviteOut)
async def create_invite(
    pair: PairByToken, user: CurrentUser, session: SessionDep
) -> InviteOut:
    """Выдать ссылку-приглашение для второй стороны.

    Живое приглашение не перевыпускается: повторный запрос возвращает тот же
    токен и тот же срок, иначе ссылка, уже отправленную партнёру, молча
    перестала бы работать.

    409, если партнёр уже пришёл — звать некого.
    """
    try:
        token, expires_at = await pairs_service.create_invite(session, pair)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Пара уже собрана") from exc

    # Токена в событии нет: он по смыслу пароль, а строки аналитики живут
    # вечно и уезжают в дампы.
    await events.log(
        session,
        events.PAIR_INVITE_CREATED,
        user_id=user.id,
        pair_id=pair.id,
        kind=pair.kind.value,
    )
    await session.commit()

    return InviteOut(invite_token=token, expires_at=expires_at)


def _out(pair: Pair, *, chart_a: Chart | None = None, chart_b: Chart | None = None) -> PairOut:
    """Пара наружу: поля строки плюс подписи сторон и состояние приглашения.

    Карты передаются явно только сразу после создания пары, когда связи ещё
    не загружены; в остальных случаях их подтянул сервис (см. _with_sides).
    """
    side_a = chart_a if chart_a is not None else pair.chart_a
    side_b = chart_b if chart_b is not None else pair.chart_b
    return PairOut(
        token=pair.token,
        kind=pair.kind,
        is_complete=pair.is_complete,
        created_at=pair.created_at,
        person_a=side_a.person_name if side_a is not None else None,
        person_b=side_b.person_name if side_b is not None else None,
        invite_open=pairs_service.invite_is_open(pair),
    )
