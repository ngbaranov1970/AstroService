"""JSON-маршруты покупки: завести платёж и узнать, чем он кончился.

Слой тонкий, как и соседи: принять запрос, позвать app/services/orders.py,
отдать схему. Те же вызовы делает Jinja-витрина в app/web/routes/pay.py —
это второй вход в ту же покупку, для веб-фронта на Vue.

ЭТИ МАРШРУТЫ ЗАКРЫТЫ ДЛЯ VK

Внутри Mini App цифровые товары продаются только за «голоса», а на iOS
запрещены вовсе, поэтому VK-пользователю здесь 403. Проверка стоит в API,
а не только в вёрстке: фронт один на все платформы, и запрет, живущий в
кнопке, снимается открытием чужой вкладки.

ВОЗВРАЩАЕТ ЧЕЛОВЕКА НЕ ЭТОТ ФАЙЛ

`return_url` ведёт на серверную страницу /pay/<token>/done: она сверяет
платёж и ставит текст в очередь, даже если вебхук не дошёл, и только потом
уводит обратно во фронт (app/web/routes/pay.py).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import generation
from app.api.deps import Caller, ChartByToken, CurrentCaller, SessionDep
from app.config import get_settings
from app.models import Chart, Order, OrderStatus, ReportType
from app.payments import yookassa
from app.schemas.orders import OrderCreated, OrderOut
from app.services import auth
from app.services import orders as orders_service
from app.services import reports as reports_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["orders"])

# Что продаётся, если фронт не назвал тип. Единственный платный вид на
# сегодня; прайс живёт в services/orders.py.
DEFAULT_TYPE = ReportType.FULL_NATAL


@router.post("/charts/{token}/orders", response_model=OrderCreated)
async def create_order(
    chart: ChartByToken, caller: CurrentCaller, session: SessionDep
) -> OrderCreated:
    """Завести заказ и платёж, вернуть адрес формы ЮKassa.

    Повторное нажатие безопасно: незакрытый заказ возвращается вместе с уже
    выданной ссылкой на форму, второго платежа не создаётся.

    Отвечает 403 из Mini App и на чужую карту, 409 — если разбор уже оплачен,
    503 — если платежи не настроены или их API недоступен.
    """
    _forbid_vk(caller)
    user = caller.user

    if chart.user_id != user.id:
        # Заказ оформляется на владельца карты, и посторонний с ссылкой не
        # должен заводить ему платежи.
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Это чужая карта")

    report_type = DEFAULT_TYPE
    if await orders_service.has_paid(
        session, user_id=user.id, chart_id=chart.id, report_type=report_type
    ):
        # 409, а не тихий успех: платить второй раз за тот же текст не за что,
        # и фронт по этому коду уводит читать купленное.
        raise HTTPException(status.HTTP_409_CONFLICT, "Этот разбор уже оплачен")

    try:
        order = await orders_service.get_or_create_order(
            session, user=user, chart=chart, report_type=report_type
        )
        # Заказ мог достаться от прошлого захода вместе со ссылкой на форму.
        # ЮKassa закрывает неоплаченные платежи сама, а уведомление об этом
        # могло не дойти — без проверки человек при каждом нажатии уезжал бы
        # на мёртвую форму (та же развилка, что в web/routes/pay.py).
        if order.confirmation_url:
            await orders_service.sync_status(session, order)
            if order.is_paid:
                await session.commit()
                raise HTTPException(status.HTTP_409_CONFLICT, "Этот разбор уже оплачен")
            if order.status is not OrderStatus.PENDING:
                # Платёж закрыт, платить по нему нечем: заводим новый заказ.
                # Тем же он быть не может — ключом идемпотентности служит его
                # токен, и ЮKassa вернула бы по нему тот же мёртвый платёж.
                order = await orders_service.get_or_create_order(
                    session, user=user, chart=chart, report_type=report_type
                )
        confirmation_url = await orders_service.start_payment(
            session, order, return_url=_return_url(chart.token, order.token)
        )
    except (orders_service.OrderError, yookassa.PaymentError) as e:
        # Заказ мог успеть записаться, а платёж — нет; откатываем, чтобы не
        # копить строки, за которые никто не платил.
        chart_id = chart.id
        await session.rollback()
        logger.warning("Оплата карты %s не началась: %s", chart_id, e)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Не получилось перейти к оплате. Попробуйте ещё раз через минуту.",
        ) from e

    # Коммит ДО ответа: следом человек уходит на чужой сайт, и без
    # сохранённого provider_payment_id вебхук не найдёт заказ.
    await session.commit()
    return OrderCreated(token=order.token, confirmation_url=confirmation_url)


@router.get("/orders/{order_token}", response_model=OrderOut)
async def get_order(
    order_token: str,
    caller: CurrentCaller,
    session: SessionDep,
    background: BackgroundTasks,
) -> OrderOut:
    """Состояние заказа для экрана возврата с оплаты.

    Статус спрашивается у ЮKassa: возврат человека сам по себе ничего не
    доказывает, а вебхук идёт своим темпом. Если деньги на месте, а текста
    ещё нет, здесь же ставится генерация — на случай, когда и вебхук, и
    страница возврата не сработали.

    Отвечает 403 из Mini App и на чужой заказ, 404 — если заказа нет.
    """
    _forbid_vk(caller)
    user = caller.user

    order = await orders_service.get_by_token(session, order_token)
    if order is None or order.user_id != user.id:
        # Чужой заказ показываем как несуществующий: по коду ответа не должно
        # угадываться, есть ли такой токен.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Такого заказа нет")

    if order.status is OrderStatus.PENDING:
        try:
            await orders_service.sync_status(session, order)
            await session.commit()
        except yookassa.PaymentError:
            # Их API недоступен — платёж не потерян, его подтвердит вебхук.
            # Экран в этом случае честно пишет «проверяем оплату».
            await session.rollback()
            logger.warning("Заказ %s: статус спросить не удалось", order.id)
            order = await orders_service.get_by_token(session, order_token)
            if order is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Такого заказа нет")

    if order.is_paid:
        await _ensure_generation(session, background, order)

    return _out(order)


# --- Внутренние помощники --------------------------------------------------


def _forbid_vk(caller: Caller) -> None:
    """403 открывшему Mini App: продавать внутри него нельзя.

    Смотрим на площадку сессии, а не на users.platform: вошедший через VK ID
    сидит в браузере со строкой платформы VK, и оплата ему доступна.
    """
    if caller.surface == auth.SURFACE_VK:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Оплата доступна только на сайте, вне приложения VK",
        )


def _return_url(chart_token: str, order_token: str) -> str:
    """Адрес, куда ЮKassa вернёт человека с формы оплаты.

    Абсолютный: относительный путь на их домене не годится. Домен берём из
    настроек, а не из заголовков запроса — Host подделывается клиентом.
    Бросает OrderError, если домен не задан.
    """
    base = get_settings().public_base_url
    if base is None:
        raise orders_service.OrderError("В .env не задан PUBLIC_BASE_URL — платить некуда")
    return (
        f"{base.rstrip('/')}/pay/{chart_token}/done"
        f"?order={order_token}&back={orders_service.BACK_APP}"
    )


async def _ensure_generation(
    session: AsyncSession, background: BackgroundTasks, order: Order
) -> None:
    """Поставить оплаченный разбор в очередь, если текста ещё нет.

    Проверка «текста нет» обязательна: экран возврата фронт опрашивает, и
    без неё каждый опрос плодил бы фоновую задачу.
    """
    if order.chart_id is None:
        # Карту удалили вместе с персональными данными — генерировать нечего.
        return
    if await reports_service.get_report(session, order.chart_id, order.report_type):
        return

    chart = await session.get(Chart, order.chart_id)
    if chart is None:
        return
    generation.schedule(background, chart, order.report_type)


def _out(order: Order) -> OrderOut:
    """Собрать ответ по строке заказа."""
    return OrderOut(
        token=order.token,
        number=order.id,
        status=order.status,
        paid=order.is_paid,
        report_type=order.report_type,
        title=orders_service.title(order.report_type),
        price_rub=order.amount // 100,
        created_at=order.created_at,
        paid_at=order.paid_at,
    )
