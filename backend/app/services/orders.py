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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Chart, Order, OrderStatus, Pair, ReportType, User
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
    # 390 ₽ — пересчитано от якоря 290 ₽ за полный натал: совместимость
    # дороже одиночного разбора, но остаётся в том же ряду цен.
    ReportType.PAIR_FULL: 39_000,
}

# Что человек видит в форме оплаты и в выписке банка. Персональных данных
# тут быть не может по устройству: название товара одно на всех — имён
# сторон в названии парного разбора нет по этой же причине.
TITLES: dict[ReportType, str] = {
    ReportType.FULL_NATAL: "Полный разбор натальной карты",
    ReportType.PAIR_FULL: "Разбор совместимости",
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


# --- Куда возвращать человека после оплаты ---------------------------------
# Значения параметра `back` страницы /pay/<token>/done. Платить уходят из
# приложения во внешний браузер, и вернуться туда же само собой не выйдет:
# страница возврата обязана знать, откуда пришли. Строки лежат здесь, потому
# что их пишет один слой (api/routes/orders.py, фронт), а читает другой
# (web/routes/pay.py), и разъехавшись, они молча перестанут совпадать.
BACK_APP = "app"  # веб-фронт на Vue: возвращаем редиректом в /app/
BACK_VK = "vk"    # Mini App: ссылкой vk.ru/app<id>, редирект туда не годится


def payment_url(token: str, report_type: ReportType) -> str | None:
    """Адрес страницы оплаты для кнопки «купить». None — платежи не настроены.

    token — публичный токен карты или пары, смотря что покупают; страница
    /pay/<token> сама разбирается, что ей дали.

    Без ключей ЮKassa или PUBLIC_BASE_URL кнопка привела бы человека на
    страницу, которая честно скажет «не получилось перейти к оплате»; лучше
    не звать вовсе, чем звать в тупик.

    Адрес абсолютный и всегда ведёт на сайт: из VK и Telegram платить уходят
    во внешний браузер, а туда путь без домена не годится.
    """
    settings = get_settings()
    base = settings.public_base_url
    if not settings.payments_enabled or base is None:
        return None
    return f"{base.rstrip('/')}/pay/{token}?type={report_type.value}"


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
    chart_id: int | None = None,
    report_type: ReportType,
    pair_id: int | None = None,
) -> bool:
    """Оплачен ли этим человеком этот разбор этой карты или этой пары.

    Адрес покупки ровно один: либо chart_id, либо pair_id — иначе запрос
    нашёл бы любой заказ человека с таким типом и открыл доступ к чужому
    тексту. Бросает ValueError, если задано и то, и другое или ничего.

    Купленный разбор одной карты не открывает разбор второй, и точно так же
    с парами: платят за текст, а не за подписку.

    Возврат (REFUNDED) снова закрывает доступ: статус меняется, и запрос
    перестаёт находить строку.
    """
    if (chart_id is None) == (pair_id is None):
        raise ValueError("has_paid: нужен ровно один адрес — chart_id или pair_id")

    stmt = select(Order.id).where(
        Order.user_id == user_id,
        Order.report_type == report_type,
        Order.status == OrderStatus.PAID,
    )
    if pair_id is not None:
        stmt = stmt.where(Order.pair_id == pair_id)
    else:
        # pair_id IS NULL обязателен: у парного заказа chart_id заполнен
        # картой инициатора, и без этого условия покупка «я + Маша» открыла
        # бы «я + Лена» — карта-якорь и тип у них одни и те же.
        stmt = stmt.where(Order.chart_id == chart_id, Order.pair_id.is_(None))
    return await session.scalar(stmt.limit(1)) is not None


async def get_or_create_order(
    session: AsyncSession,
    *,
    user: User,
    chart: Chart | None = None,
    report_type: ReportType,
    pair: Pair | None = None,
) -> Order:
    """Заказ на этот разбор: уже существующий или новый.

    Покупают либо разбор карты (chart), либо разбор пары (pair) — ровно
    одно из двух, иначе ValueError. У парного заказа заполнены обе колонки:
    chart_id хранит карту инициатора, чтобы заказ попал в «мои покупки» и в
    каскады, а платят по pair_id.

    Существующий возвращается в двух случаях — он оплачен (значит, платить
    больше не за что) или ещё ждёт оплаты (человек вернулся по ссылке или
    нажал кнопку второй раз). Отменённые и возвращённые пропускаем: по ним
    заводится новый заказ, потому что платёж в ЮKassa у отменённого мёртв.

    Транзакцию не коммитим — как и в соседних сервисах, это дело вызывающего.
    Бросает NotForSaleError и ValueError.
    """
    amount = price(report_type)

    # Развилка адреса. Ветвями, а не одним условием: так типизатор видит,
    # что в каждой ветке нужный объект не None.
    if pair is not None:
        if chart is not None:
            raise ValueError("get_or_create_order: chart и pair сразу")
        if pair.user_id != user.id:
            raise ValueError("pair belongs to another user")
        # Карта-якорь у парного заказа — сторона A. Берём из pair.chart_a_id,
        # а не запросом в базу: значение уже в объекте.
        chart_id = pair.chart_a_id
        pair_id = pair.id
    elif chart is not None:
        chart_id = chart.id
        pair_id = None
    else:
        raise ValueError("get_or_create_order: нужен chart или pair")

    async def find_open() -> Order | None:
        # Свежие вперёд: если заказов накопилось несколько, работаем с последним.
        stmt = select(Order).where(
            Order.user_id == user.id,
            Order.report_type == report_type,
            Order.status.in_((OrderStatus.PAID, OrderStatus.PENDING)),
        )
        # Тот же разбор адреса, что в has_paid: без условия на pair_id
        # ждущий заказ на одну пару нашёлся бы как заказ на другую.
        if pair_id is not None:
            stmt = stmt.where(Order.pair_id == pair_id)
        else:
            stmt = stmt.where(Order.chart_id == chart_id, Order.pair_id.is_(None))
        return await session.scalar(stmt.order_by(Order.id.desc()).limit(1))

    existing = await find_open()
    if existing is not None:
        return existing

    order = Order(
        user_id=user.id,
        chart_id=chart_id,
        pair_id=pair_id,
        report_type=report_type,
        amount=amount,
        status=OrderStatus.PENDING,
    )
    # Вставка под SAVEPOINT — тот же приём, что в charts.create_chart(). Два
    # одновременных нажатия «купить» оба не находят заказа и оба вставляют;
    # частичный уникальный индекс не даёт записать второй ждущий заказ, а
    # без савпоинта его ошибка обрушила бы всю транзакцию («current
    # transaction is aborted») вместо одной вставки.
    try:
        async with session.begin_nested():
            session.add(order)
    except IntegrityError:
        conflicting = await find_open()
        if conflicting is not None:
            logger.info(
                "Заказ на «%s» (карта %s, пара %s) уже завёл параллельный "
                "запрос — беру его",
                report_type.value, chart_id, pair_id,
            )
            return conflicting
        # Конфликт не про дубль заказа (например, карту успели удалить) —
        # глушить нельзя, это ошибка в коде вызывающего.
        raise

    await events.log(
        session,
        events.ORDER_CREATED,
        user_id=user.id,
        order_id=order.id,
        report_type=report_type.value,
        amount=amount,
    )
    logger.info(
        "Заказ %s создан: %s, %d коп., пользователь %s, карта %s, пара %s",
        order.id, report_type.value, amount, user.id, chart_id, pair_id,
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

    saved_url = order.confirmation_url
    if saved_url:
        logger.info("Заказ %s уже ведёт в ЮKassa, ссылку не пересоздаём", order.id)
        return saved_url

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

    # Идентификатор платежа сохраняем первым делом: платёж у них уже создан,
    # и без этой связи его не найдёт ни вебхук, ни разбор спора.
    order.provider_payment_id = payment.id
    order.provider_data = payment.raw

    confirmation_url = payment.confirmation_url
    if confirmation_url is None:
        # Платёж создан, а вести человека некуда — платить он не сможет.
        raise OrderError("ЮKassa не вернула ссылку на оплату")
    order.confirmation_url = confirmation_url

    await events.log(
        session,
        events.PAYMENT_STARTED,
        user_id=order.user_id,
        order_id=order.id,
        report_type=order.report_type.value,
        amount=order.amount,
    )
    return confirmation_url


async def apply_notification(
    session: AsyncSession, payment_id: str
) -> tuple[Order | None, bool]:
    """Обработать уведомление вебхука: сверить платёж с API и зачислить.

    Возвращает заказ и признак «деньги зачислены ИМЕННО ЭТИМ вызовом».
    Признак нужен потому, что уведомление приходит по нескольку раз (ЮKassa
    доставляет минимум однажды), и по нему вызывающий отличает настоящую
    оплату от повтора — иначе генерация и события повторялись бы следом.

    Заказ равен None, если платёж нам неизвестен (чужой магазин, выдуманный
    id, заказ ещё не сохранён). Это не авария: вызывающий всё равно отвечает
    200, иначе ЮKassa будет повторять уведомление сутки.

    Бросает PaymentError, если их API недоступен — вот тогда 200 отдавать
    нельзя, повтор уведомления и есть наш второй шанс.
    """
    try:
        payment = await yookassa.get_payment(payment_id)
    except yookassa.PaymentNotFoundError:
        # Магазин про этот платёж не знает: уведомление подделано или пришло
        # чужому магазину. Повтор ответа не изменит, поэтому не ошибка.
        logger.warning("Уведомление о платеже %s: магазину он неизвестен", payment_id)
        return None, False

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
        return None, False

    just_paid = await _apply_payment(session, order, payment)
    return order, just_paid


async def sync_status(session: AsyncSession, order: Order) -> Order:
    """Спросить ЮKassa о платеже этого заказа и привести статус в соответствие.

    Нужна на странице возврата: вебхук идёт своим темпом, а человек уже
    смотрит на экран и хочет увидеть купленное. Ничего не делает, если
    платежа ещё нет или заказ уже закрыт.

    Платёж, неизвестный магазину, закрывает заказ (CANCELED), а не бросает
    ошибку: платить по нему нечем, и вызывающий заведёт новый.

    Бросает PaymentError.
    """
    if order.provider_payment_id is None or order.status is not OrderStatus.PENDING:
        return order

    payment_id = order.provider_payment_id
    try:
        payment = await yookassa.get_payment(payment_id)
    except yookassa.PaymentNotFoundError:
        # Платежа у магазина нет — заказ мёртв, и оставить его ждущим нельзя:
        # он единственный открытый, а спросить его статус больше нечем, так
        # что кнопка «купить» упиралась бы в него навсегда.
        await session.refresh(order, with_for_update=True)
        if order.status is OrderStatus.PENDING:
            order.status = OrderStatus.CANCELED
            logger.warning(
                "Заказ %s отменён: платёж %s магазину неизвестен",
                order.id, payment_id,
            )
        return order

    # Перечитываем строку под блокировкой, и только теперь применяем платёж:
    # вебхук про этот же платёж приходит одновременно с возвратом человека и
    # мог уже поставить PAID. Без этого оба пути видят PENDING и оба пишут
    # order_paid — деньги целы, а выручка по событиям задвоена (проверено).
    # Блокировка берётся ПОСЛЕ запроса к их API: он длится до 30 с, и всё это
    # время вебхук ждал бы освобождения строки.
    await session.refresh(order, with_for_update=True)
    await _apply_payment(session, order, payment)
    return order


async def _apply_payment(
    session: AsyncSession, order: Order, payment: yookassa.Payment
) -> bool:
    """Перенести состояние платежа на заказ. Единственное место, где ставится PAID.

    Возвращает True только когда деньги зачислены этим вызовом. По нему
    вызывающий решает, запускать ли генерацию: уведомление приходит по
    нескольку раз, и «заказ оплачен» не то же самое, что «оплатили сейчас».

    Идемпотентна: повторный вызов на уже закрытом заказе ничего не меняет и
    события не пишет — вебхук приходит по нескольку раз.
    """
    # Сырой ответ обновляем всегда: даже когда статус не поменялся, это
    # свежий след для разбора спора.
    order.provider_data = payment.raw

    if order.status is not OrderStatus.PENDING:
        logger.debug("Платёж %s: заказ %s уже закрыт, пропускаем", payment.id, order.id)
        return False

    if payment.status == yookassa.STATUS_CANCELED:
        order.status = OrderStatus.CANCELED
        logger.info("Заказ %s отменён: платёж %s canceled", order.id, payment.id)
        return False

    # Деньги открывают доступ, а не статус: succeeded с paid=false у них
    # означает оплату, отменённую до зачисления.
    if payment.status != yookassa.STATUS_SUCCEEDED or not payment.paid:
        return False

    if not _amount_matches(payment.amount_rub, order.amount):
        # Доступ не открываем и заказ не трогаем: расхождение сумм означает,
        # что платёж не тот, за который мы его принимаем. Разбирается руками
        # по этой строке лога и provider_data.
        logger.error(
            "Заказ %s: платёж %s пришёл на %s ₽ вместо %s ₽ — доступ НЕ открыт",
            order.id, payment.id, payment.amount_rub, order.amount_rub,
        )
        return False

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
    return True


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
