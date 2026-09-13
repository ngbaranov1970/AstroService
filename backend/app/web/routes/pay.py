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
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from app import generation
from app.config import get_settings
from app.db import get_session
from app.models import Chart, Order, OrderStatus, Pair, ReportType, User
from app.payments import yookassa
from app.services import charts, events, orders, pairs, reports, users
from app.web import deps
from app.web.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter(include_in_schema=False)

SessionDep = Annotated[AsyncSession, Depends(get_session)]

# Что продаётся по умолчанию, если тип не указан в адресе. Умолчаний два,
# потому что входов два: по токену карты покупают натальный разбор, по
# токену пары — совместимость. Список цен живёт в services/orders.py.
DEFAULT_TYPE = ReportType.FULL_NATAL
DEFAULT_PAIR_TYPE = ReportType.PAIR_FULL

# Платные типы, адресуемые парой. Реестр в services/reports.py закрытый
# (_PAIR_TYPES), а здесь нужна только платная его часть.
_PAIR_TYPES = frozenset({ReportType.PAIR_FULL})

# Откуда раздаётся собранный Vue-фронт. Та же строка стоит в `base` в
# frontend/vite.config.js — разойдясь, они дадут возврат с оплаты в никуда.
APP_BASE_PATH = "/app/"


# --- Страницы оплаты -------------------------------------------------------


@router.get("/pay/{token}", dependencies=[Depends(deps.forbid_in_vk)])
async def pay_page(
    request: Request,
    token: str,
    session: SessionDep,
    report_type: Annotated[str, Query(alias="type")] = "",
    back: Annotated[str, Query()] = "",
) -> Response:
    """Витрина заказа: что покупается, за сколько, кнопка и ссылка на оферту.

    Только читает. Платёж заводится по нажатию кнопки (POST) — GET не должен
    ходить в чужой API: этот адрес открывают и предпросмотр ссылки в
    мессенджере, и обновление страницы.

    `back` говорит, откуда пришёл человек; форма несёт его дальше, чтобы
    страница возврата знала, куда его вернуть.
    """
    target = await _context(session, token, report_type)

    paid = await orders.has_paid(
        session,
        user_id=target.owner.id,
        report_type=target.rtype,
        **target.address,
    )
    return templates.TemplateResponse(
        request, "pay.html", _page_context(target, paid=paid, back=_back(back))
    )


@router.post("/pay/{token}", dependencies=[Depends(deps.forbid_in_vk)])
async def pay_start(
    request: Request,
    token: str,
    session: SessionDep,
    report_type: Annotated[str, Form()] = "",
    back: Annotated[str, Form()] = "",
) -> Response:
    """Нажата кнопка оплаты: заводим заказ, платёж и уводим на форму ЮKassa."""
    target = await _context(session, token, report_type)
    back = _back(back)

    if await orders.has_paid(
        session,
        user_id=target.owner.id,
        report_type=target.rtype,
        **target.address,
    ):
        # Платить второй раз за тот же разбор не даём: отправляем читать.
        return RedirectResponse(_app_url(target.app_path), status.HTTP_303_SEE_OTHER)

    try:
        order = await _new_order(session, target)
        # Заказ мог достаться от прошлого захода вместе со ссылкой на форму
        # оплаты. ЮKassa закрывает неоплаченные платежи сама, а уведомление
        # об этом могло не дойти — тогда без проверки человек при каждом
        # нажатии возвращался бы на мёртвую форму и купить не смог бы вовсе.
        if order.confirmation_url:
            await orders.sync_status(session, order)
            if order.is_paid:
                await session.commit()
                return RedirectResponse(
                    _app_url(target.app_path), status.HTTP_303_SEE_OTHER
                )
            if order.status is not OrderStatus.PENDING:
                # Платёж закрыт, платить по нему нечем: заводим новый заказ.
                # Тем же он быть не может — ключом идемпотентности служит его
                # токен, и ЮKassa вернула бы по нему тот же мёртвый платёж.
                order = await _new_order(session, target)
        confirmation_url = await orders.start_payment(
            session,
            order,
            # Абсолютный адрес обязателен: ЮKassa возвращает человека из
            # своего домена, относительный путь ей не годится.
            return_url=_absolute(f"/pay/{token}/done?order={order.token}&back={back}"),
        )
    except (orders.OrderError, yookassa.PaymentError) as e:
        # Заказ мог успеть записаться, а платёж — нет; откатываем, чтобы не
        # копить строки, за которые никто не платил.
        subject = f"пары {target.pair.id}" if target.pair else f"карты {target.chart.id}"
        await session.rollback()
        logger.warning("Оплата %s не началась: %s", subject, e)
        # Перечитываем всё: откат помечает объекты устаревшими, а их поля
        # дальше читает шаблон — там async-контекста уже нет, и догрузка
        # падает с MissingGreenlet, то есть 500 вместо страницы с ошибкой.
        target = await _context(session, token, report_type)
        return templates.TemplateResponse(
            request,
            "pay.html",
            _page_context(target, paid=False, back=back, error=_HUMAN_ERROR),
        )

    # Коммит ДО редиректа: за ним человек уходит на чужой сайт, и если
    # provider_payment_id не сохранён, вебхук не найдёт заказ.
    await session.commit()
    return RedirectResponse(confirmation_url, status.HTTP_303_SEE_OTHER)


@router.get("/pay/{token}/done", dependencies=[Depends(deps.forbid_in_vk)])
async def pay_done(
    request: Request,
    token: str,
    session: SessionDep,
    background: BackgroundTasks,
    order_token: Annotated[str, Query(alias="order")] = "",
    back: Annotated[str, Query()] = "",
) -> Response:
    """Сюда ЮKassa возвращает человека с формы оплаты.

    Возврат сам по себе ничего не доказывает — его легко не дождаться и так
    же легко открыть руками, — поэтому статус спрашивается у их API
    (sync_status). Вебхук делает то же самое своим темпом; кто успел первым,
    неважно.

    `back=app` означает, что платить пришли из веб-фронта: тогда страница
    ничего не рисует, а уводит редиректом обратно в приложение.
    """
    back = _back(back)
    # Тип не передаём: здесь он неизвестен, а нужен только для витрины.
    # _context подставит умолчание того входа, который нашёлся по токену.
    target = await _context(session, token, "")

    order = await orders.get_by_token(session, order_token) if order_token else None
    if order is not None and not _order_matches(order, target):
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
            target = await _context(session, token, "")
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
        if target.pair is not None:
            if await reports.get_pair_report(
                session, target.pair.id, order.report_type
            ) is None:
                generation.schedule_pair(background, target.pair, order.report_type)
        elif await reports.get_report(session, target.chart.id, order.report_type) is None:
            generation.schedule(background, target.chart, order.report_type)

    if back == orders.BACK_APP:
        # Человек платил из веб-фронта: свой экран «оплачено» он ждёт там, и
        # показать ему страницу стенда — значит выкинуть из приложения.
        # Токен заказа передаём: по нему тот экран берёт номер и сумму.
        return RedirectResponse(
            _app_url(f"/paid/{token}", order=order_token),
            status.HTTP_303_SEE_OTHER,
        )

    return templates.TemplateResponse(
        request,
        "pay_done.html",
        {
            "chart": target.chart,
            "is_pair": target.pair is not None,
            "chart_token": token,
            "chart_url": _app_url(target.app_path),
            "order": order,
            "paid": paid,
            "title": orders.title(order.report_type if order else target.rtype),
            # Имя владельца — только у разбора карты: страницу открывает кто
            # угодно по ссылке, а за парой стоят данные двоих.
            "owner_name": target.chart.person_name if target.pair is None else None,
            # Ссылка обратно в Mini App — только для пришедших из VK: у
            # остальных приложения, куда возвращаться, нет.
            "vk_return_url": _vk_return_url(target) if back == orders.BACK_VK else None,
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


@dataclass(frozen=True, slots=True)
class _Target:
    """Что оплачивают: разбор карты или разбор пары.

    `chart` заполнен всегда — у парной покупки это сторона A, она же якорь
    заказа (см. get_or_create_order). Отличает покупки `pair`.
    """

    chart: Chart
    pair: Pair | None
    owner: User
    rtype: ReportType

    @property
    def token(self) -> str:
        """Токен из адреса страницы — пары или карты, смотря что покупают."""
        return self.pair.token if self.pair is not None else self.chart.token

    @property
    def app_path(self) -> str:
        """Куда во фронте вернуть человека — на экран пары или карты."""
        kind = "pair" if self.pair is not None else "chart"
        return f"/{kind}/{self.token}"

    @property
    def address(self) -> dict[str, int]:
        """Адрес покупки для orders: ровно одно из chart_id и pair_id.

        Словарём, а не двумя аргументами: в services/orders.py оба адреса
        взаимоисключающие, и передача обоих сразу там ValueError.
        """
        if self.pair is not None:
            return {"pair_id": self.pair.id}
        return {"chart_id": self.chart.id}


async def _context(session: AsyncSession, token: str, report_type: str) -> _Target:
    """Что покупают, кому это принадлежит и какой это тип разбора. Иначе 404.

    Токен в адресе бывает и картой, и парой — форма маршрута от этого не
    меняется. Резолвим картой, а парой только если карты нет: у пар токенов
    на порядки меньше, и лишний запрос достаётся редкому случаю.

    Владелец нужен потому, что заказ и доступ принадлежат ему, а не тому,
    кто открыл ссылку на оплату (см. шапку модуля).
    """
    pair = None
    chart = await charts.get_chart_by_token(session, token)
    if chart is None:
        pair = await pairs.get_pair_for_payment(session, token)
        if pair is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Такой карты нет")
        if not pair.is_complete:
            # Партнёр ещё не пришёл, разбора не существует — платить не за
            # что. 404, а не объяснение: страницу открывает кто угодно.
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Такой карты нет")
        # Якорь парного заказа — сторона A. Через session.get, а не
        # pair.chart_a: связи объявлены lazy="raise_on_sql".
        anchor = await session.get(Chart, pair.chart_a_id)
        if anchor is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Такой карты нет")
        chart = anchor

    rtype = _report_type(report_type, is_pair=pair is not None)

    owner = await users.get_by_id(session, chart.user_id)
    if owner is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Такой карты нет")

    return _Target(chart=chart, pair=pair, owner=owner, rtype=rtype)


def _report_type(value: str, *, is_pair: bool) -> ReportType:
    """Тип разбора из адреса; пустая строка означает «обычный для этого входа».

    Умолчание зависит от входа: у карты продаётся полный натальный разбор, у
    пары — разбор совместимости. Зашитая константа на всех отправила бы
    /pay/<токен пары> без параметра искать карту и отвечать 404.

    404 на неизвестный, неплатный и не подходящий входу тип: парный разбор
    не купить по токену карты, а натальный — по токену пары, иначе заказ
    ушёл бы по адресу, где отчёта никогда не появится.
    """
    if not value:
        return DEFAULT_PAIR_TYPE if is_pair else DEFAULT_TYPE

    try:
        rtype = ReportType(value)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Неизвестный вид разбора")

    if not orders.is_for_sale(rtype):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Этот разбор не продаётся")

    if (rtype in _PAIR_TYPES) != is_pair:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Неизвестный вид разбора")

    return rtype


def _back(value: str) -> str:
    """Оставить от параметра `back` только известное значение, иначе пусто.

    Строка уезжает в return_url и в скрытое поле формы; произвольную оттуда
    вернут нам же обратно, и на странице возврата она станет чужим адресом.
    """
    return value if value in (orders.BACK_APP, orders.BACK_VK) else ""


def _app_url(path: str, *, order: str | None = None) -> str:
    """Адрес экрана веб-фронта: /app/#/<path>, с номером заказа, если он есть.

    Берётся из APP_BASE_URL, а без неё строится относительным путём на том же
    домене. Настройка нужна потому, что вернуться человек может не туда, где
    лежит фронт: адрес возврата ЮKassa запомнила при создании платежа, и
    сменившийся с тех пор PUBLIC_BASE_URL приводил бы его на 404.

    Решётка обязательна — роутер фронта работает в hash-режиме, чтобы
    переходы не стирали параметры запуска VK.
    """
    base = get_settings().app_base_url
    root = base.rstrip("/") if base else APP_BASE_PATH.rstrip("/")
    tail = f"?order={order}" if order else ""
    return f"{root}/#{path}{tail}"


async def _new_order(session: AsyncSession, target: _Target) -> Order:
    """Завести заказ по адресу покупки — на карту или на пару.

    Развилка здесь, а не в двух местах вызова: в services/orders.py chart и
    pair взаимоисключающие, и передача обоих сразу там ValueError.
    """
    if target.pair is not None:
        return await orders.get_or_create_order(
            session, user=target.owner, pair=target.pair, report_type=target.rtype
        )
    return await orders.get_or_create_order(
        session, user=target.owner, chart=target.chart, report_type=target.rtype
    )


def _order_matches(order: Order, target: _Target) -> bool:
    """Тот ли это заказ, за который платили на этой странице.

    Обе колонки сравниваются обязательно: у парного заказа chart_id тоже
    заполнен — это якорная карта инициатора, общая у всех его пар. Проверка
    по одной карте приняла бы заказ на «я и Маша» за оплату «я и Лены».
    """
    pair_id = target.pair.id if target.pair is not None else None
    return order.chart_id == target.chart.id and order.pair_id == pair_id


def _vk_return_url(target: _Target) -> str | None:
    """Ссылка обратно в Mini App на экран купленного разбора. None — id не задан.

    Открывается она в браузере телефона, а VK уже перекидывает в приложение.
    """
    app_id = get_settings().vk_app_id
    if app_id is None:
        return None
    return f"https://vk.com/app{app_id}#/report/{target.token}/{target.rtype.value}"


def _page_context(
    target: _Target,
    *,
    paid: bool,
    back: str = "",
    error: str | None = None,
) -> dict:
    """Данные витрины оплаты — одним словарём для шаблона."""
    is_pair = target.pair is not None
    return {
        "chart": target.chart,
        # Шаблон по нему решает, показывать ли данные рождения: у пары их на
        # странице нет — ссылку открывает кто угодно, а за парой двое людей.
        "is_pair": is_pair,
        "chart_token": target.token,
        # Готовый адрес экрана во фронте: шаблон собрать его не может —
        # корень фронта живёт в настройках, а не в шаблоне.
        "chart_url": _app_url(target.app_path),
        "report_type": target.rtype.value,
        "back": back,
        "title": orders.title(target.rtype),
        "price": orders.price_rub(target.rtype),
        "titles": reports.section_titles(target.rtype),
        "person_name": target.chart.person_name if not is_pair else None,
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

    Карту и пару достаём отдельно: у оплаченного заказа они могут быть уже
    удалены (колонки обнуляются, чтобы заказ пережил удаление персональных
    данных), и тогда генерировать нечего.
    """
    if order.pair_id is not None:
        pair = await session.get(Pair, order.pair_id)
        # Пару сносит каскадом удаление любой из двух карт, поэтому проверка
        # на собранность тут не лишняя: calc у половинки пустой.
        if pair is None or not pair.is_complete:
            return
        await events.log(
            session,
            events.REPORT_REQUESTED,
            user_id=order.user_id,
            pair_id=pair.id,
            type=order.report_type.value,
        )
        await session.commit()
        generation.schedule_pair(background, pair, order.report_type)
        return

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
