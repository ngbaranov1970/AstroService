"""Пары: сборка из двух карт, расчёт синастрии, приглашение партнёра.

Для пары то же, чем charts.py является для карты: склейка чистой функции
compute_synastry (умеет только «два натала → словарь») с таблицей pairs.

Про HTTP, VK и Telegram здесь нет ни слова — граница из CLAUDE.md.

Стороны не симметричны и не нормализуются: A — инициатор, B — партнёр.
Почему так и почему пара живёт с пустой стороной B — см. docstring модели
app/models/pair.py и docs/compatibility-plan.md.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chart, Pair, PairKind
from astro_core.synastry import VERSION, compute_synastry

# Пишется в Pair.calc_version. Это версия ТОЛЬКО наших правил синастрии —
# орбов, состава точек и формулы веса. Версий kerykeion и pyswisseph здесь
# нет намеренно: позиции планет приходят готовыми из charts.calc, и их
# происхождение уже записано в Chart.calc_version.
CALC_VERSION = VERSION

# Сколько живёт ссылка-приглашение. Без срока забытое приглашение остаётся
# открытым входом в аккаунт инициатора навсегда.
INVITE_TTL = timedelta(days=7)


def _synastry(chart_a: Chart, chart_b: Chart) -> dict:
    """Считает синастрию по двум сохранённым картам.

    Флаги времени берём из колонок time_known, а не из расчёта: в
    calc["natal"]["angles"] Асцендент и MC лежат всегда, в том числе
    посчитанные от подставленных 12:00, и отличить их по данным нельзя.
    """
    # Вызов синхронный, без asyncio.to_thread — в отличие от compute_natal
    # в charts.py. Здесь нет эфемерид, только разности долгот: замер на
    # золотых картах — 0.23 мс, уход в поток стоил бы столько же.
    return compute_synastry(
        chart_a.calc["natal"],
        chart_b.calc["natal"],
        a_time_known=chart_a.time_known,
        b_time_known=chart_b.time_known,
    )


async def _find_complete(
    session: AsyncSession,
    *,
    user_id: int,
    chart_a_id: int,
    chart_b_id: int,
    kind: PairKind,
) -> Pair | None:
    """Собранная пара с точно таким составом, если она уже есть.

    Набор полей повторяет частичный уникальный индекс uq_pairs_complete —
    искать надо ровно тем ключом, которым база потом не даст вставить.
    """
    stmt = select(Pair).where(
        Pair.user_id == user_id,
        Pair.chart_a_id == chart_a_id,
        Pair.chart_b_id == chart_b_id,
        Pair.kind == kind,
    )
    return await session.scalar(stmt)


async def create_pair(
    session: AsyncSession,
    *,
    user_id: int,
    chart_a: Chart,
    chart_b: Chart | None = None,
    kind: PairKind,
    inviter_label: str | None = None,
) -> Pair:
    """Главная функция модуля: две карты и тип связи → строка в pairs.

    Без chart_b создаётся пара, ждущая партнёра (calc пуст), и дедупа у неё
    нет: незавершённых приглашений на одну карту законно бывает несколько.
    С chart_b возвращает уже существующую пару того же состава, если она была.
    Транзакцию не коммитит — это делает вызывающий, см. get_session в app/db.py.

    Бросает ValueError, если карта принадлежит другому пользователю.
    """
    # Обе карты обязаны принадлежать инициатору: карту партнёра он заводит
    # у себя в аккаунте. Проверка здесь, а не только в API, потому что
    # chart_id приходит снаружи, а по нему видна чужая дата рождения.
    for chart in (chart_a, chart_b):
        if chart is not None and chart.user_id != user_id:
            raise ValueError("chart belongs to another user")

    if chart_b is not None and chart_b.id == chart_a.id:
        raise ValueError("pair of a chart with itself")

    if chart_b is not None:
        existing = await _find_complete(
            session,
            user_id=user_id,
            chart_a_id=chart_a.id,
            chart_b_id=chart_b.id,
            kind=kind,
        )
        if existing is not None:
            return existing

    pair = Pair(
        user_id=user_id,
        chart_a_id=chart_a.id,
        chart_b_id=chart_b.id if chart_b is not None else None,
        kind=kind,
        # Обрезаем под String(64): подпись приходит снаружи и не должна
        # ронять вставку.
        inviter_label=inviter_label[:64] if inviter_label else None,
        calc=_synastry(chart_a, chart_b) if chart_b is not None else None,
        calc_version=CALC_VERSION if chart_b is not None else None,
    )

    try:
        # begin_nested() — это SAVEPOINT. Без него неудачная вставка ломает
        # всю транзакцию: Postgres ответит «current transaction is aborted»
        # на любой следующий запрос.
        async with session.begin_nested():
            session.add(pair)
        return pair
    except IntegrityError:
        # Тот же человек отправил форму дважды, и второй запрос упёрся в
        # uq_pairs_complete. У незавершённой пары такого индекса нет, поэтому
        # искать есть смысл только когда сторона B задана.
        if chart_b is not None:
            existing = await _find_complete(
                session,
                user_id=user_id,
                chart_a_id=chart_a.id,
                chart_b_id=chart_b.id,
                kind=kind,
            )
            if existing is not None:
                return existing
        # Ошибка не про дубль пары (например, несуществующий user_id) —
        # глушить нельзя, это ошибка в коде вызывающего.
        raise


def invite_is_open(pair: Pair, *, now: datetime | None = None) -> bool:
    """Можно ли сейчас войти по приглашению этой пары.

    Три условия сразу: токен выдан, срок не вышел, партнёр ещё не пришёл.
    Чистая проверка без базы — её же зовут и роутер инвайтов, и attach_partner.
    """
    if pair.invite_token is None or pair.invite_used_at is not None:
        return False
    if pair.chart_b_id is not None:
        return False
    if pair.invite_expires_at is None:
        return False
    return pair.invite_expires_at > (now or datetime.now(UTC))


async def create_invite(session: AsyncSession, pair: Pair) -> str:
    """Выдаёт ссылке-приглашению токен и срок, возвращает токен.

    Живое приглашение не перевыпускается: поле одно, и новый токен молча
    сломал бы ссылку, уже отправленную партнёру.

    Бросает ValueError, если партнёр уже пришёл — звать некого.
    """
    if pair.chart_b_id is not None:
        raise ValueError("pair is already complete")

    # Токен держим в локальной переменной и её же возвращаем. Колонка
    # объявлена str | None, а сужения через invite_is_open типизатор не
    # видит — с прямым `return pair.invite_token` Pylance ругается на тип.
    issued = pair.invite_token
    if issued is not None and invite_is_open(pair):
        return issued

    # 24 случайных байта, как у Chart.token: токен по смыслу пароль, и
    # перебором его не достать. В логи не пишем нигде и никогда.
    issued = secrets.token_urlsafe(24)
    pair.invite_token = issued
    pair.invite_expires_at = datetime.now(UTC) + INVITE_TTL
    pair.invite_used_at = None
    await session.flush()
    return issued


async def get_pair_by_invite(session: AsyncSession, invite_token: str) -> Pair | None:
    """Пара по токену приглашения — вход для анонимного экрана партнёра.

    None означает и «нет такого токена», и «протух», и «уже использован»:
    вызывающий обязан отвечать на все три одинаковым 404, иначе разные
    ответы подтверждают существование токена тому, кто перебирает.
    """
    pair = await session.scalar(select(Pair).where(Pair.invite_token == invite_token))
    if pair is None or not invite_is_open(pair):
        return None
    return pair


async def attach_partner(session: AsyncSession, pair: Pair, chart_b: Chart) -> Pair:
    """Ставит вторую сторону пары и считает синастрию. Приход по приглашению.

    Возвращает заполненную пару — эту же или ту, что уже была с таким
    составом. Повторный вызов на занятой паре ничего не меняет.
    """
    if chart_b.user_id != pair.user_id:
        raise ValueError("chart belongs to another user")
    if chart_b.id == pair.chart_a_id:
        raise ValueError("pair of a chart with itself")

    # session.get, а не pair.chart_a: связи объявлены lazy="raise_on_sql" и
    # обращение к ним из async-кода бросает исключение.
    chart_a = await session.get(Chart, pair.chart_a_id)
    if chart_a is None:
        raise ValueError("side A chart is gone")

    stmt = (
        update(Pair)
        # Условие chart_b_id IS NULL — и есть замок от «открыл ссылку в двух
        # вкладках»: второй UPDATE не найдёт строки и вернёт rowcount 0.
        .where(Pair.id == pair.id, Pair.chart_b_id.is_(None))
        .values(
            chart_b_id=chart_b.id,
            calc=_synastry(chart_a, chart_b),
            calc_version=CALC_VERSION,
            invite_used_at=func.now(),
        )
        .execution_options(synchronize_session=False)
    )

    try:
        async with session.begin_nested():
            await session.execute(stmt)
    except IntegrityError:
        # Пара такого состава у инициатора уже есть: он успел ввести данные
        # партнёра руками, пока тот шёл по ссылке. Отдаём собранную, а
        # приглашение гасим — иначе ссылка останется открытым входом.
        existing = await _find_complete(
            session,
            user_id=pair.user_id,
            chart_a_id=pair.chart_a_id,
            chart_b_id=chart_b.id,
            kind=pair.kind,
        )
        if existing is None:
            raise
        await _close_invite(session, pair)
        return existing

    # rowcount == 0 означает «партнёр уже пришёл в соседней вкладке»: строку
    # не тронули, вторая карта не перебита. Отдельной ветки не нужно, в
    # обоих случаях объект в сессии устарел после UPDATE мимо ORM.
    await session.refresh(pair)
    return pair


async def _close_invite(session: AsyncSession, pair: Pair) -> None:
    """Гасит приглашение: снимает токен и отмечает момент использования."""
    pair.invite_token = None
    pair.invite_used_at = datetime.now(UTC)
    await session.flush()


async def get_pair(
    session: AsyncSession, pair_id: int, *, user_id: int | None = None
) -> Pair | None:
    """Пара по идентификатору, None — если её нет.

    user_id — проверка владения, передавать всегда, когда идентификатор
    пришёл снаружи: за парой стоят данные рождения двух человек.
    """
    stmt = select(Pair).where(Pair.id == pair_id)
    if user_id is not None:
        stmt = stmt.where(Pair.user_id == user_id)
    return await session.scalar(stmt)


async def get_pair_by_token(session: AsyncSession, token: str, *, user_id: int) -> Pair | None:
    """Пара по публичному токену, None — если её нет или она чужая.

    Владелец проверяется обязательно — осознанное отличие от
    get_chart_by_token, где проверки нет намеренно. Карта — данные одного
    человека, и он сам решает, кому переслать ссылку; пара — данные двоих,
    и второй ссылку не давал.
    """
    stmt = select(Pair).where(Pair.token == token, Pair.user_id == user_id)
    return await session.scalar(stmt)


async def list_pairs(session: AsyncSession, user_id: int) -> list[Pair]:
    """Все пары пользователя, свежие первыми.

    Ждущие партнёра тоже здесь: на экране они и есть «приглашение отправлено».
    """
    stmt = select(Pair).where(Pair.user_id == user_id).order_by(Pair.created_at.desc())
    return list(await session.scalars(stmt))
