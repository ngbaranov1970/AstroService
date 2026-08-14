"""Заказы: намерение купить разбор и след того, чем оно кончилось.

Место в цепочке:

    reports.needs_purchase()  →  ЭТОТ МОДУЛЬ  →  payments/yookassa.py

Слева — «этот отчёт закрыт», справа — чужой платёжный API. Здесь: прайс,
строка в orders, зачисление денег. Про HTTP, шаблоны и Telegram ни слова,
как и в соседних сервисах: те же вызовы сделает сайт, бот и Mini App.

ГЛАВНОЕ ПРАВИЛО МОДУЛЯ: ДОСТУП ОТКРЫВАЮТ ДЕНЬГИ, А НЕ ВОЗВРАТ ЧЕЛОВЕКА

Человек, вернувшийся с формы банка на нашу страницу «спасибо», не доказал
ничего: редирект легко не дождаться (закрыл вкладку) и так же легко
подделать, открыв адрес руками. Поэтому статус PAID ставится только после
ответа api.yookassa.ru на НАШ запрос о платеже — и в вебхуке, и при
возврате человека путь один и тот же (_apply_payment).

ПОЧЕМУ НЕТ ГАЛОЧКИ «ОПЛАЧЕНО» В reports

Заказов на один отчёт может быть несколько (первый раз не прошла карта,
второй прошёл), и каждый — денежный факт для разбора спора. Галочка хранит
только последний исход. Отсюда же способ проверить доступ: has_paid()
ищет оплаченный заказ, а не смотрит на отчёт.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chart, Order, OrderStatus, ReportType, User
from app.payments import yookassa
from app.services import events

logger = logging.getLogger(__name__)


class OrderError(RuntimeError):
    """Заказ создать или оплатить нельзя — с человеческой причиной внутри."""


class NotForSaleError(OrderError):
    """У этого типа отчёта нет цены: он либо бесплатный, либо ещё не готов."""


# --- Прайс -----------------------------------------------------------------
# В КОПЕЙКАХ: 290 ₽ → 29000. Причина та же, по которой копейки в модели, —
# дробями деньги не считают. Здесь же и витрина: страница берёт цену и
# название отсюда, а не пишет своими словами.
#
# 290 ₽ — та же цифра, что в оферте (web/legal.py импортирует её отсюда):
# разойдясь, они дадут повод для спора, в котором мы неправы по документу.
PRICES: dict[ReportType, int] = {
    ReportType.FULL_NATAL: 29_000,
}

# Что человек видит в форме оплаты и в выписке банка. Персональных данных
# тут быть не может по устройству: название товара одно на всех.
TITLES: dict[ReportType, str] = {
    ReportType.FULL_NATAL: "Полный разбор натальной карты",
}


def price(report_type: ReportType) -> int:
    """Цена в копейках. Бросает NotForSaleError, если тип не продаётся."""
    try:
        return PRICES[report_type]
    except KeyError as e:
        raise NotForSaleError(f"Тип отчёта {report_type.value} не продаётся") from e


def price_rub(report_type: ReportType) -> int:
    """Цена в целых рублях — для показа на странице (копеек в прайсе нет)."""
    return price(report_type) // 100


def title(report_type: ReportType) -> str:
    """Название товара для формы оплаты и кнопки покупки."""
    return TITLES.get(report_type, "Астрологический разбор")


def is_for_sale(report_type: ReportType) -> bool:
    """Есть ли у типа цена. Интерфейсу — рисовать кнопку или молчать."""
    return report_type in PRICES


async def get_by_token(session: AsyncSession, token: str) -> Order | None:
    """Заказ по публичному токену из ссылки /pay/<token>.

    Проверки владельца тут нет намеренно, как и у карты: токен и есть ключ
    (24 случайных байта), а по последовательному id подставлялся бы соседний
    чужой заказ.
    """
    return await session.scalar(select(Order).where(Order.token == token))


async def has_paid(
    session: AsyncSession,
    *,
    user_id: int,
    chart_id: int,
    report_type: ReportType,
) -> bool:
    """Оплачен ли этим человеком этот разбор этой карты.

    Именно тройка: заказ на чужую карту доступа к своей не даёт, а купленный
    полный разбор одной карты не открывает полный разбор второй — платят за
    текст, а не за подписку.

    Возврат (REFUNDED) снова закрывает доступ: статус меняется, и запрос
    перестаёт находить строку.
    """
    stmt = select(Order.id).where(
        Order.user_id == user_id,
        Order.chart_id == chart_id,
        Order.report_type == report_type,
        Order.status == OrderStatus.PAID,
    )
    return await session.scalar(stmt.limit(1)) is not None


async def get_or_create_order(
    session: AsyncSession,
    *,
    user: User,
    chart: Chart,
    report_type: ReportType,
) -> Order:
    """Заказ на этот разбор: уже существующий или новый.

    Существующий возвращается в двух случаях — он оплачен (значит, платить
    больше не за что) или ещё ждёт оплаты (человек вернулся по ссылке или
    нажал кнопку второй раз). Отменённые и возвращённые пропускаем: по ним
    заводится новый заказ, потому что платёж в ЮKassa у отменённого мёртв.

    Транзакцию не коммитим — как и в соседних сервисах, это дело вызывающего.
    Бросает NotForSaleError.
    """
    amount = price(report_type)

    # Свежие вперёд: если заказов накопилось несколько, работаем с последним.
    existing = await session.scalar(
        select(Order)
        .where(
            Order.user_id == user.id,
            Order.chart_id == chart.id,
            Order.report_type == report_type,
            Order.status.in_((OrderStatus.PAID, OrderStatus.PENDING)),
        )
        .order_by(Order.id.desc())
        .limit(1)
    )
    if existing is not None:
        return existing

    order = Order(
        user_id=user.id,
        chart_id=chart.id,
        report_type=report_type,
        amount=amount,
        status=OrderStatus.PENDING,
    )
    session.add(order)
    # flush, а не commit: нужен id для события и для лога, а решение
    # «сохранять ли всё это» остаётся за вызывающим.
    await session.flush()

    await events.log(
        session,
        events.ORDER_CREATED,
        user_id=user.id,
        order_id=order.id,
        report_type=report_type.value,
        amount=amount,
    )
    logger.info(
        "Заказ %s создан: %s, %d коп., пользователь %s, карта %s",
        order.id, report_type.value, amount, user.id, chart.id,
    )
    return order


async def start_payment(
    session: AsyncSession, order: Order, *, return_url: str
) -> str:
    """Завести платёж в ЮKassa (или взять уже заведённый) и вернуть адрес формы.

    Повторный вызов на том же заказе не создаёт второго платежа: сначала
    отдаётся сохранённый confirmation_url, а если и дошло до API, ключом
    идемпотентности служит токен заказа.

    return_url строит вызывающий: адреса страниц — знание веб-слоя, сервис
    про маршруты не знает.

    Бросает OrderError (заказ уже закрыт) и PaymentError (их API).
    """
    if order.status is not OrderStatus.PENDING:
        raise OrderError(f"Заказ {order.id} не ждёт оплаты (статус {order.status.value})")

    if order.confirmation_url:
        logger.info("Заказ %s уже ведёт в ЮKassa, ссылку не пересоздаём", order.id)
        return order.confirmation_url

    payment = await yookassa.create_payment(
        amount_rub=order.amount_rub,
        description=title(order.report_type),
        return_url=return_url,
        # Токен заказа как ключ идемпотентности: он уникален, стабилен и
        # укладывается в их предел 64 символа. UUID пришлось бы хранить
        # отдельной колонкой ради того же самого.
        idempotence_key=order.token,
        # Метаданные видны в их личном кабинете — по ним заказ находится
        # при разборе спора. Только наши id, никаких ПДн.
        metadata={"order_id": str(order.id)},
    )

    order.provider_payment_id = payment.id
    order.confirmation_url = payment.confirmation_url
    order.provider_data = payment.raw

    if not payment.confirmation_url:
        # Платёж создан, а вести человека некуда: платить он не сможет, но
        # идентификатор мы уже сохранили — иначе платёж остался бы висеть
        # у них без связи с нашим заказом.
        raise OrderError("ЮKassa не вернула ссылку на оплату")

    await events.log(
        session,
        events.PAYMENT_STARTED,
        user_id=order.user_id,
        order_id=order.id,
        report_type=order.report_type.value,
        amount=order.amount,
    )
    return order.confirmation_url


async def apply_notification(session: AsyncSession, payment_id: str) -> Order | None:
    """Обработать уведомление вебхука: сверить платёж с API и зачислить.

    Возвращает затронутый заказ или None, если платёж нам неизвестен (чужой
    магазин, выдуманный id, заказ ещё не сохранён). None — не авария:
    вызывающий всё равно отвечает 200, иначе ЮKassa будет повторять
    уведомление сутки.

    Бросает PaymentError, если их API недоступен — вот тогда 200 отдавать
    нельзя, повтор уведомления и есть наш второй шанс.
    """
    payment = await yookassa.get_payment(payment_id)

    # FOR UPDATE: уведомление повторяется сутки и легко прилетает дважды
    # одновременно. Без блокировки строки оба обработчика увидели бы PENDING
    # и дважды записали событие оплаты.
    order = await session.scalar(
        select(Order)
        .where(Order.provider_payment_id == payment.id)
        .with_for_update()
    )
    if order is None:
        logger.warning("Уведомление о платеже %s: заказа с таким id нет", payment.id)
        return None

    await _apply_payment(session, order, payment)
    return order


async def sync_status(session: AsyncSession, order: Order) -> Order:
    """Спросить ЮKassa о платеже этого заказа и привести статус в соответствие.

    Нужна на странице возврата: вебхук идёт своим темпом, а человек уже
    смотрит на экран и хочет увидеть купленное. Ничего не делает, если
    платежа ещё нет или заказ уже закрыт.

    Бросает PaymentError.
    """
    if order.provider_payment_id is None or order.status is not OrderStatus.PENDING:
        return order

    payment = await yookassa.get_payment(order.provider_payment_id)
    await _apply_payment(session, order, payment)
    return order


async def _apply_payment(
    session: AsyncSession, order: Order, payment: yookassa.Payment
) -> None:
    """Перенести состояние платежа на заказ. Единственное место, где ставится PAID.

    Идемпотентна: повторный вызов на уже закрытом заказе ничего не меняет и
    события не пишет — вебхук приходит по нескольку раз.
    """
    # Сырой ответ обновляем всегда: даже когда статус не поменялся, это
    # свежий след для разбора спора.
    order.provider_data = payment.raw

    if order.status is not OrderStatus.PENDING:
        logger.debug("Платёж %s: заказ %s уже закрыт, пропускаем", payment.id, order.id)
        return

    if payment.status == yookassa.STATUS_CANCELED:
        order.status = OrderStatus.CANCELED
        logger.info("Заказ %s отменён: платёж %s canceled", order.id, payment.id)
        return

    # Деньги открывают доступ, а не статус: succeeded с paid=false у них
    # означает оплату, отменённую до зачисления.
    if payment.status != yookassa.STATUS_SUCCEEDED or not payment.paid:
        return

    if not _amount_matches(payment.amount_rub, order.amount):
        # Доступ не открываем и заказ не трогаем: расхождение сумм означает,
        # что платёж не тот, за который мы его принимаем. Разбирается руками
        # по этой строке лога и provider_data.
        logger.error(
            "Заказ %s: платёж %s пришёл на %s ₽ вместо %s ₽ — доступ НЕ открыт",
            order.id, payment.id, payment.amount_rub, order.amount_rub,
        )
        return

    order.status = OrderStatus.PAID
    order.paid_at = datetime.now(UTC)

    await events.log(
        session,
        events.ORDER_PAID,
        user_id=order.user_id,
        order_id=order.id,
        report_type=order.report_type.value,
        amount=order.amount,
    )
    # INFO с деньгами — строка, по которой сверяется выручка, если до
    # бухгалтерии дело дойдёт раньше отчётов.
    logger.info(
        "Заказ %s ОПЛАЧЕН: %s ₽, платёж %s, пользователь %s",
        order.id, order.amount_rub, payment.id, order.user_id,
    )


def _amount_matches(payment_amount_rub: str, order_amount_kop: int) -> bool:
    """Совпала ли сумма платежа с суммой заказа («290.00» против 29000).

    Через Decimal, а не float: 2.90 в двоичной дроби не представимо точно, и
    сравнение копейка в копейку через float — способ однажды не открыть
    оплаченный разбор.
    """
    try:
        return int(Decimal(payment_amount_rub) * 100) == order_amount_kop
    except (InvalidOperation, TypeError):
        return False
