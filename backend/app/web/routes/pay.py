"""Маршруты оплаты: витрина заказа, уход в ЮKassa, возврат и вебхук.

Место в слоях: браузер (и сервер ЮKassa) → ЭТОТ ФАЙЛ → services/orders.py →
payments/yookassa.py. Своей логики денег здесь нет — только маршруты,
шаблоны и коммит транзакции.

СТРАНИЦЫ ОПЛАТЫ РАБОТАЮТ БЕЗ АВТОРИЗАЦИИ, И ЭТО НЕ УПУЩЕНИЕ

Платить человек уходит во внешний браузер: внутри Mini App продавать
цифровые товары нельзя (только за «голоса», а на iOS запрещено вовсе).
В том браузере нет ни нашей cookie, ни токена `?s=` из VK, поэтому
`current_user` здесь неприменим. Ключом служит токен карты — те же 24
случайных байта, что открывают `/r/<token>`, и заказ оформляется на
ВЛАДЕЛЬЦА карты, кем бы ни был открывший ссылку. Практический смысл ровно
такой: заплатить за чужой разбор можно, получить чужой разбор — нет.

Отсюда же форма маршрутов: адрес построен на токене КАРТЫ, а не заказа —
только его страница разбора знает заранее, когда рисует кнопку.

ГЕНЕРАЦИЮ ЗАПУСКАЕТ ОПЛАТА, А НЕ ЕЩЁ ОДНА КНОПКА

Как только деньги пришли, текст ставится в очередь — и в вебхуке, и на
странице возврата, что бы из них ни случилось первым. Двойной траты это не
даёт: замок и кеш в services/reports.py считают один и тот же адрес отчёта
одной работой.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from app import generation
from app.config import get_settings
from app.db import get_session
from app.models import Chart, Order, OrderStatus, ReportType, User
from app.payments import yookassa
from app.services import charts, events, orders, reports, users
from app.web.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter(include_in_schema=False)

SessionDep = Annotated[AsyncSession, Depends(get_session)]

# Что продаётся по умолчанию, если тип не указан в адресе. Единственный
# платный тип на сегодня; список цен живёт в services/orders.py.
DEFAULT_TYPE = ReportType.FULL_NATAL


# --- Страницы оплаты -------------------------------------------------------


@router.get("/pay/{chart_token}")
async def pay_page(
    request: Request,
    chart_token: str,
    session: SessionDep,
    report_type: Annotated[str, Query(alias="type")] = DEFAULT_TYPE.value,
) -> Response:
    """Витрина заказа: что покупается, за сколько, кнопка и ссылка на оферту.

    Только читает. Платёж заводится по нажатию кнопки (POST) — GET не должен
    ходить в чужой API: этот адрес открывают и предпросмотр ссылки в
    мессенджере, и обновление страницы.
    """
    chart, owner, rtype = await _context(session, chart_token, report_type)

    paid = await orders.has_paid(
        session, user_id=owner.id, chart_id=chart.id, report_type=rtype
    )
    return templates.TemplateResponse(
        request, "pay.html", _page_context(chart, rtype, paid=paid)
    )


@router.post("/pay/{chart_token}")
async def pay_start(
    request: Request,
    chart_token: str,
    session: SessionDep,
    report_type: Annotated[str, Form()] = DEFAULT_TYPE.value,
) -> Response:
    """Нажата кнопка оплаты: заводим заказ, платёж и уводим на форму ЮKassa."""
    chart, owner, rtype = await _context(session, chart_token, report_type)

    if await orders.has_paid(
        session, user_id=owner.id, chart_id=chart.id, report_type=rtype
    ):
        # Платить второй раз за тот же разбор не даём: отправляем читать.
        return RedirectResponse(f"/r/{chart_token}", status.HTTP_303_SEE_OTHER)

    try:
        order = await orders.get_or_create_order(
            session, user=owner, chart=chart, report_type=rtype
        )
        # Заказ мог достаться от прошлого захода вместе со ссылкой на форму
        # оплаты. ЮKassa закрывает неоплаченные платежи сама, а уведомление
        # об этом могло не дойти — тогда без проверки человек при каждом
        # нажатии возвращался бы на мёртвую форму и купить не смог бы вовсе.
        if order.confirmation_url:
            await orders.sync_status(session, order)
            if order.is_paid:
                await session.commit()
                return RedirectResponse(f"/r/{chart_token}", status.HTTP_303_SEE_OTHER)
            if order.status is not OrderStatus.PENDING:
                # Платёж закрыт, платить по нему нечем: заводим новый заказ.
                # Тем же он быть не может — ключом идемпотентности служит его
                # токен, и ЮKassa вернула бы по нему тот же мёртвый платёж.
                order = await orders.get_or_create_order(
                    session, user=owner, chart=chart, report_type=rtype
                )
        confirmation_url = await orders.start_payment(
            session,
            order,
            # Абсолютный адрес обязателен: ЮKassa возвращает человека из
            # своего домена, относительный путь ей не годится.
            return_url=_absolute(f"/pay/{chart_token}/done?order={order.token}"),
        )
    except (orders.OrderError, yookassa.PaymentError) as e:
        # Заказ мог успеть записаться, а платёж — нет; откатываем, чтобы не
        # копить строки, за которые никто не платил.
        chart_id = chart.id
        await session.rollback()
        logger.warning("Оплата карты %s не началась: %s", chart_id, e)
        # Перечитываем карту: откат помечает объекты устаревшими, а её поля
        # дальше читает шаблон — там async-контекста уже нет, и догрузка
        # падает с MissingGreenlet, то есть 500 вместо страницы с ошибкой.
        chart, _owner, _rtype = await _context(session, chart_token, report_type)
        return templates.TemplateResponse(
            request,
            "pay.html",
            _page_context(chart, rtype, paid=False, error=_HUMAN_ERROR),
        )

    # Коммит ДО редиректа: за ним человек уходит на чужой сайт, и если
    # provider_payment_id не сохранён, вебхук не найдёт заказ.
    await session.commit()
    return RedirectResponse(confirmation_url, status.HTTP_303_SEE_OTHER)


@router.get("/pay/{chart_token}/done")
async def pay_done(
    request: Request,
    chart_token: str,
    session: SessionDep,
    background: BackgroundTasks,
    order_token: Annotated[str, Query(alias="order")] = "",
) -> Response:
    """Сюда ЮKassa возвращает человека с формы оплаты.

    Возврат сам по себе ничего не доказывает — его легко не дождаться и так
    же легко открыть руками, — поэтому статус спрашивается у их API
    (sync_status). Вебхук делает то же самое своим темпом; кто успел первым,
    неважно.
    """
    chart, _owner, _rtype = await _context(session, chart_token, DEFAULT_TYPE.value)

    order = await orders.get_by_token(session, order_token) if order_token else None
    if order is not None and order.chart_id != chart.id:
        # Токен заказа из чужой ссылки: показываем так, будто его нет.
        order = None

    if order is not None:
        order_id = order.id
        try:
            await orders.sync_status(session, order)
            await session.commit()
        except yookassa.PaymentError:
            # Их API недоступен — платёж не потерян, его подтвердит вебхук.
            # Человеку в этом случае честно пишем «проверяем».
            await session.rollback()
            logger.warning("Заказ %s: статус спросить не удалось", order_id)
            # Перечитываем оба объекта по той же причине, что и карту выше:
            # после отката шаблон читает и номер заказа, и имя владельца
            # карты — уже вне async-контекста, где догрузка падает.
            chart, _owner, _rtype = await _context(
                session, chart_token, DEFAULT_TYPE.value
            )
            order = await orders.get_by_token(session, order_token)

    paid = order is not None and order.is_paid
    if paid and order is not None:
        # Текст начинает писаться сразу: пока человек читает «спасибо»,
        # DeepSeek уже работает, и на странице разбора ждать меньше.
        #
        # Ставим только когда текста ещё нет. Здесь нельзя опереться на
        # «оплатили сейчас», как в вебхуке: страница обязана остаться вторым
        # шансом, если деньги зачислил вебхук, а генерация после него упала.
        # Иначе же обновление страницы плодило бы фоновые задачи.
        if await reports.get_report(session, chart.id, order.report_type) is None:
            generation.schedule(background, chart, order.report_type)

    return templates.TemplateResponse(
        request,
        "pay_done.html",
        {
            "chart": chart,
            "chart_token": chart_token,
            "order": order,
            "paid": paid,
            "title": orders.title(order.report_type if order else DEFAULT_TYPE),
            "owner_name": chart.person_name,
        },
    )


# --- Вебхук ----------------------------------------------------------------


@router.post("/webhooks/yookassa")
async def yookassa_webhook(
    request: Request, session: SessionDep, background: BackgroundTasks
) -> Response:
    """Уведомление от ЮKassa — единственное, что открывает отчёт.

    Отвечаем 200 почти всегда: на любой другой код они повторяют уведомление
    сутки. Исключение — их же API недоступен: тогда 503, и повтор становится
    нашим вторым шансом.

    Проверки подписи здесь нет, потому что подписи не существует: тело
    уведомления служит сигналом, а состояние платежа берётся запросом к API
    (см. services/orders.py).
    """
    try:
        body = await request.json()
    except ValueError:
        logger.warning("Вебхук ЮKassa: тело не разбирается как JSON")
        return Response(status_code=status.HTTP_200_OK)

    payment_id = yookassa.notification_payment_id(body)
    if payment_id is None:
        # Уведомление не про платёж (возврат, выплата) — нам такие не нужны,
        # но подтвердить получение надо, иначе они будут слать его сутки.
        return Response(status_code=status.HTTP_200_OK)

    try:
        order, just_paid = await orders.apply_notification(session, payment_id)
    except yookassa.PaymentError:
        await session.rollback()
        logger.error("Вебхук ЮKassa: платёж %s проверить не удалось", payment_id)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Повторите позже")

    await session.commit()

    # Генерацию запускает ЗАЧИСЛЕНИЕ, а не оплаченность заказа: то же
    # уведомление ЮKassa присылает по нескольку раз, и на «is_paid» повтор
    # заводил бы лишнюю фоновую задачу и лишние report_requested/report_ready.
    if order is not None and just_paid:
        await _start_generation(session, background, order)
    return Response(status_code=status.HTTP_200_OK)


# --- Внутренние помощники --------------------------------------------------

# Одна фраза на все сбои оплаты: человеку незачем знать, отказал ли их API
# или мы не смогли записать заказ, а лишние подробности на платёжной
# странице читаются как «тут что-то сломано, не платите».
_HUMAN_ERROR = "Не получилось перейти к оплате. Попробуйте ещё раз через минуту."


async def _context(
    session: AsyncSession, chart_token: str, report_type: str
) -> tuple[Chart, User, ReportType]:
    """Карта, её владелец и тип отчёта — по данным из адреса. Иначе 404.

    Владелец нужен потому, что заказ и доступ принадлежат ему, а не тому,
    кто открыл ссылку на оплату (см. шапку модуля).
    """
    chart = await charts.get_chart_by_token(session, chart_token)
    if chart is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Такой карты нет")

    try:
        rtype = ReportType(report_type)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Неизвестный вид разбора")

    if not orders.is_for_sale(rtype):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Этот разбор не продаётся")

    owner = await users.get_by_id(session, chart.user_id)
    if owner is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Такой карты нет")

    return chart, owner, rtype


def _page_context(
    chart: Chart, report_type: ReportType, *, paid: bool, error: str | None = None
) -> dict:
    """Данные витрины оплаты — одним словарём для шаблона."""
    return {
        "chart": chart,
        "chart_token": chart.token,
        "report_type": report_type.value,
        "title": orders.title(report_type),
        "price": orders.price_rub(report_type),
        "titles": reports.section_titles(report_type),
        "person_name": chart.person_name,
        "paid": paid,
        "error": error,
    }


def _absolute(path: str) -> str:
    """Адрес нашей страницы с доменом. Бросает OrderError, если домен не задан.

    Домен берём из настроек, а не из заголовков запроса: Host подделывается
    клиентом, и подделанный уехал бы в ЮKassa как адрес возврата.
    """
    base = get_settings().public_base_url
    if base is None:
        raise orders.OrderError("В .env не задан PUBLIC_BASE_URL — платить некуда")
    return f"{base.rstrip('/')}{path}"


async def _start_generation(
    session: AsyncSession, background: BackgroundTasks, order: Order
) -> None:
    """Поставить оплаченный разбор в очередь на генерацию.

    Карту достаём отдельно: у оплаченного заказа она может быть уже удалена
    (chart_id обнуляется, чтобы заказ пережил удаление персональных данных),
    и тогда генерировать нечего.
    """
    if order.chart_id is None:
        return
    chart = await session.get(Chart, order.chart_id)
    if chart is None:
        return

    await events.log(
        session,
        events.REPORT_REQUESTED,
        user_id=order.user_id,
        chart_id=chart.id,
        type=order.report_type.value,
    )
    await session.commit()
    generation.schedule(background, chart, order.report_type)
