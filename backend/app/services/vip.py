"""VIP-разбор: заказ, который владелец заводит в админке по данным из лички.

Место в цепочке: web/routes/admin.py → ЭТОТ МОДУЛЬ → users, charts, orders.
Здесь склейка «VK ID + данные рождения → пользователь, карта, заказ» и
выборки для экрана заказов. Деньги и платёж — в orders.py, страницы — в web/.
Почему VIP продаётся только по заказу, а не открытой кнопкой, —
docs/sales-policy.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Chart, Order, OrderStatus, Platform, ReportType, User
from app.services import charts, orders, users

logger = logging.getLogger(__name__)

# Метка канала у людей, впервые попавших в базу через заказ из лички:
# по ней их видно отдельно от пришедших в приложение.
SOURCE = "vip_dm"

# Срок готовности от оплаты — то, что обещано в переписке (docs/sales-policy.md).
DELIVERY_TIME = timedelta(hours=24)

# Потолок строк на экране заказов: при паре продаж в день это месяцы.
LIST_LIMIT = 100


@dataclass(frozen=True, slots=True)
class VipOrder:
    """Заказ с покупателем и картой — всё, что нужно строке и карточке.

    `chart` бывает None: карту человек вправе удалить, а заказ остаётся.
    """

    order: Order
    user: User
    chart: Chart | None

    @property
    def due_at(self) -> datetime | None:
        """Когда разбор должен уйти в личку. None — ещё не оплачен."""
        paid_at = self.order.paid_at
        return paid_at + DELIVERY_TIME if paid_at is not None else None

    @property
    def in_work(self) -> bool:
        """Оплачен и не отправлен — отдельного статуса нет, см. docs/db-schema.md."""
        return self.order.status is OrderStatus.PAID and self.order.delivered_at is None


def payment_url(order: Order) -> str | None:
    """Ссылка на оплату для лички. None — платежи или PUBLIC_BASE_URL не настроены.

    Адрес всегда абсолютный и на нашем домене: в переписке другой не годится.
    Маршрут — web/routes/pay.py, vip_pay_page.
    """
    settings = get_settings()
    base = settings.public_base_url
    if not settings.payments_enabled or base is None:
        return None
    return f"{base.rstrip('/')}/pay/order/{order.token}"


async def create_order(
    session: AsyncSession,
    *,
    vk_user_id: int,
    birth_local: datetime,
    time_known: bool,
    place: charts.Place,
    person_name: str | None = None,
) -> Order:
    """Завести VIP-заказ на человека из диалога сообщества.

    Повтор с теми же данными не плодит строк: карта находится по input_hash,
    а заказ возвращается уже существующий — ждущий оплаты или ОПЛАЧЕННЫЙ
    (второй VIP на ту же карту так не завести, и это намеренно).
    Транзакцию не коммитит. Бросает NotForSaleError, если VIP убрали из прайса.
    """
    user = await users.get_or_create(
        session, platform=Platform.VK, external_id=str(vk_user_id), source=SOURCE
    )
    # Имя из профиля — чтобы в списке заказов стоял человек, а не номер.
    await users.fill_vk_name(user)

    chart = await charts.create_chart(
        session,
        user_id=user.id,
        birth_local=birth_local,
        place=place,
        time_known=time_known,
    )
    if person_name and chart.person_name != person_name:
        chart.person_name = person_name

    order = await orders.get_or_create_order(
        session, user=user, chart=chart, report_type=ReportType.VIP_NATAL
    )
    logger.info(
        "VIP-заказ %s (%s): пользователь %s, карта %s",
        order.id, order.status.value, user.id, chart.id,
    )
    return order


async def list_orders(session: AsyncSession, limit: int = LIST_LIMIT) -> list[VipOrder]:
    """VIP-заказы, свежие первыми, кроме отменённых.

    Отменённые прячем: это мёртвые платежи по той же ссылке, за каждым из
    которых уже стоит новый ждущий заказ.
    """
    stmt = (
        select(Order, User, Chart)
        .join(User, User.id == Order.user_id)
        .outerjoin(Chart, Chart.id == Order.chart_id)
        .where(
            Order.report_type == ReportType.VIP_NATAL,
            Order.status != OrderStatus.CANCELED,
        )
        .order_by(Order.created_at.desc())
        .limit(limit)
    )
    return [VipOrder(order=o, user=u, chart=c) for o, u, c in await session.execute(stmt)]


async def get_order(session: AsyncSession, order_id: int) -> VipOrder | None:
    """Один VIP-заказ по внутреннему id. None — нет такого или он не VIP."""
    row = (
        await session.execute(
            select(Order, User, Chart)
            .join(User, User.id == Order.user_id)
            .outerjoin(Chart, Chart.id == Order.chart_id)
            .where(Order.id == order_id, Order.report_type == ReportType.VIP_NATAL)
        )
    ).first()
    if row is None:
        return None
    order, user, chart = row
    return VipOrder(order=order, user=user, chart=chart)
