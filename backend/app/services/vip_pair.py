"""Заказ разбора пары из VK-бота: партнёр, заказ vip_pair и расчёт по нему.

Место в цепочке: chatbot/flows/pair.py, pair_teaser, vip_draft, web/routes/pay.py
→ ЭТОТ МОДУЛЬ → charts, orders, astro_core. Партнёр живёт только в
orders.pair_data: ни users, ни charts, ни pairs под него не заводятся.
Синастрия не хранится — считается по требованию из двух наталов.
Текст и доставка — в vip_draft.py, тизер — в pair_teaser.py.
Транзакцию функции не коммитят. Решения — docs/vk-bot-plan.md, раздел ПАРА.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chart, Order, OrderStatus, ReportType, User
from app.services import charts, events, orders
from astro_core.natal import compute_natal
from astro_core.synastry import compute_synastry

logger = logging.getLogger(__name__)

_NAME_MAX = 60


@dataclass(frozen=True, slots=True)
class Partner:
    """Вторая сторона пары, как её ввёл клиент.

    birth_time None — время неизвестно; place None — города не спрашивали
    (его спрашивают только при известном времени).
    """

    birth_date: date
    birth_time: time | None = None
    place: charts.Place | None = None
    name: str | None = None


async def partner_data(
    partner: Partner, *, client_place: charts.Place, ref: str | None
) -> dict[str, Any]:
    """Партнёр → словарь для orders.pair_data, вместе с посчитанным наталом.

    Без времени считаем на 12:00, без города — в городе клиента: планетам,
    кроме Луны, место не важно, а Луне нужен хотя бы тот же часовой пояс.
    """
    place = partner.place or client_place
    clock = partner.birth_time or charts.UNKNOWN_TIME
    birth_local = datetime.combine(partner.birth_date, clock)
    # compute_natal синхронный и считает эфемериды — в поток, как в charts.create_chart.
    natal = await asyncio.to_thread(compute_natal, birth_local, place.lat, place.lon, place.tz)
    return {
        "ref": ref,
        "name": (partner.name or "").strip()[:_NAME_MAX] or None,
        "date": partner.birth_date.isoformat(),
        "time": partner.birth_time.strftime("%H:%M") if partner.birth_time else None,
        "time_known": partner.birth_time is not None,
        "place": {"label": place.label, "lat": place.lat, "lon": place.lon, "tz": place.tz},
        "place_known": partner.place is not None,
        "natal": natal,
    }


def partner_key(pair_data: dict[str, Any]) -> tuple:
    """По чему два заказа считаются заказами на одного партнёра: дата, время, место.

    Имени в ключе нет: «Саша» и «Александр» с одной датой — тот же человек.
    """
    place = pair_data.get("place") or {}
    return (
        pair_data.get("date"),
        pair_data.get("time"),
        round(float(place.get("lat", 0.0)), 4),
        round(float(place.get("lon", 0.0)), 4),
    )


def time_known(pair_data: dict[str, Any]) -> bool:
    return bool(pair_data.get("time_known"))


def synastry(chart: Chart, pair_data: dict[str, Any]) -> dict:
    """Синастрия клиента (сторона A) и партнёра из pair_data (сторона B).

    Флаги времени берём из колонки и из pair_data, а не из расчёта — причина
    та же, что в services/pairs._synastry.
    """
    return compute_synastry(
        chart.calc["natal"],
        pair_data["natal"],
        a_time_known=chart.time_known,
        b_time_known=time_known(pair_data),
    )


async def _orders_of(
    session: AsyncSession, user_id: int, status: OrderStatus
) -> list[Order]:
    stmt = (
        select(Order)
        .where(
            Order.user_id == user_id,
            Order.report_type == ReportType.VIP_PAIR,
            Order.status == status,
        )
        .order_by(Order.id.desc())
    )
    return list(await session.scalars(stmt))


async def find_paid(
    session: AsyncSession, *, user_id: int, chart_id: int | None, pair_data: dict[str, Any]
) -> Order | None:
    """Оплаченный заказ этого человека на эту же карту и этого же партнёра.

    Сравнение по партнёру, а не по карте клиента: иначе вторая пара того же
    человека показалась бы оплаченной.
    """
    key = partner_key(pair_data)
    for order in await _orders_of(session, user_id, OrderStatus.PAID):
        if order.chart_id == chart_id and order.pair_data and partner_key(order.pair_data) == key:
            return order
    return None


async def create_order(
    session: AsyncSession,
    *,
    user: User,
    birth_local: datetime,
    birth_time_known: bool,
    place: charts.Place,
    partner: Partner,
    ref: str | None,
) -> Order:
    """Заказ vip_pair: оплаченный на того же партнёра, ждущий или новый.

    Ждущий заказ у клиента один: новая пара с другим партнёром переписывает
    его pair_data и сбрасывает черновик. Оплаченные не трогает.
    Бросает NotForSaleError, если пару убрали из прайса.
    """
    # Цену проверяем до расчётов: пары нет в прайсе — считать незачем.
    orders.price(ReportType.VIP_PAIR)
    chart = await charts.create_chart(
        session,
        user_id=user.id,
        birth_local=birth_local,
        place=place,
        time_known=birth_time_known,
    )
    data = await partner_data(partner, client_place=place, ref=ref)

    paid = await find_paid(session, user_id=user.id, chart_id=chart.id, pair_data=data)
    if paid is not None:
        return paid
    return await _upsert_pending(session, user_id=user.id, chart_id=chart.id, data=data)


async def reopen(session: AsyncSession, order: Order) -> Order:
    """Заказ, по которому сейчас можно платить за эту же пару.

    Сам order, пока он ждёт или оплачен. Его платёж мёртв (CANCELED) —
    оплаченный заказ на того же партнёра, иначе ждущий с тем же pair_data и
    черновиком. Обычный orders.get_or_create_order тут не годится: он завёл
    бы заказ без партнёра.
    """
    if order.status in (OrderStatus.PENDING, OrderStatus.PAID):
        return order
    if order.pair_data is None:
        raise orders.OrderError(f"У заказа {order.id} нет данных пары")
    paid = await find_paid(
        session, user_id=order.user_id, chart_id=order.chart_id, pair_data=order.pair_data
    )
    if paid is not None:
        return paid
    live = await _upsert_pending(
        session, user_id=order.user_id, chart_id=order.chart_id, data=order.pair_data
    )
    # Вычитанный текст не теряем: у свежего заказа черновика ещё нет.
    if live.draft_text is None:
        live.draft_text, live.draft_candidate = order.draft_text, order.draft_candidate
    return live


async def _upsert_pending(
    session: AsyncSession, *, user_id: int, chart_id: int | None, data: dict[str, Any]
) -> Order:
    """Единственный ждущий заказ пары этого человека: переписать или завести."""
    amount = orders.price(ReportType.VIP_PAIR)
    for attempt in (1, 2):
        pending = await _orders_of(session, user_id, OrderStatus.PENDING)
        if pending:
            order = pending[0]
            same = (
                order.chart_id == chart_id
                and order.pair_data is not None
                and partner_key(order.pair_data) == partner_key(data)
            )
            if not same:
                # Черновик писался по другой паре — отправить его новому партнёру нельзя.
                order.draft_text = None
                order.draft_candidate = None
                order.chart_id = chart_id
            # Имя и ref обновляем и у того же партнёра: повтор исправляет опечатку.
            order.pair_data = data
            logger.info(
                "Заказ пары %s: ждущий, пользователь %s, партнёр %s",
                order.id, user_id, "тот же" if same else "переписан",
            )
            return order

        try:
            return await _insert(
                session, user_id=user_id, chart_id=chart_id, data=data, amount=amount
            )
        except IntegrityError:
            # Двойное нажатие: параллельный запрос вставил ждущий заказ первым
            # (uq_orders_one_pending) — на втором круге берём его.
            if attempt == 2:
                raise
    raise AssertionError("unreachable")


async def _insert(
    session: AsyncSession,
    *,
    user_id: int,
    chart_id: int | None,
    data: dict[str, Any],
    amount: int,
) -> Order:
    order = Order(
        user_id=user_id,
        chart_id=chart_id,
        report_type=ReportType.VIP_PAIR,
        amount=amount,
        status=OrderStatus.PENDING,
        pair_data=data,
    )
    # SAVEPOINT: ошибка уникального индекса иначе обрушила бы всю транзакцию.
    async with session.begin_nested():
        session.add(order)

    await events.log(
        session,
        events.ORDER_CREATED,
        user_id=user_id,
        order_id=order.id,
        report_type=ReportType.VIP_PAIR.value,
        amount=amount,
    )
    logger.info("Заказ пары %s создан: %d коп., пользователь %s", order.id, amount, user_id)
    return order
