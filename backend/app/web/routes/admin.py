"""Маршруты админки стенда: что сгенерировано, чем один текст лучше другого,
очередь постов сообщества и VIP-заказы из лички.

Отдельный роутер, а не секция в router.py: `require_admin` висит на всём
APIRouter сразу, поэтому забыть охрану на новом маршруте невозможно — а за
страницами имена, даты и места рождения живых людей. Выборки живут в
app/services/admin.py и app/services/content.py, здесь только разбор
параметров и шаблон.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Form,
    HTTPException,
    Query,
    Request,
    status,
)
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from app import generation
from app.db import get_session
from app.models import ClientGender, Platform, ReportType
from app.schemas.charts import ChartIn
from app.services import admin as admin_service
from app.services import broadcast, card, content, events, orders, reports, vip, vip_draft
from app.services import charts as charts_service
from app.services import users as users_service
from app.vk import api as vk_api
from app.web.deps import require_admin
from app.web.templating import templates
from astro_core import transits

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin",
    # Зависимость в dependencies, а не в аргументах хендлеров: имя вошедшего
    # нам не нужно, нужен сам факт проверки — и он теперь общий для префикса.
    dependencies=[Depends(require_admin)],
    include_in_schema=False,
)

SessionDep = Annotated[AsyncSession, Depends(get_session)]

# Потолок строк в таблице: двести отчётов — это уже месяцы обкатки промптов,
# а страница остаётся одним экраном HTML без пагинации.
LIST_LIMIT = 200

# Сколько текстов помещается рядом, чтобы колонка осталась читаемой.
MAX_COMPARE = 4

# Период автообновления страницы контента, пока идёт прогон. Реже, чем 5 с у
# страницы разбора: там ждут минуту, здесь — минут десять, и лишние сотни
# перезагрузок с полным SELECT пачки ни к чему.
CONTENT_REFRESH_SECONDS = 15

# Горизонт пачки: 7 дней прогона плюс восьмой — на него адресован вечерний
# гороскоп последнего вечера недели (sign_lines пишется на завтра).
CONTENT_DAYS = 8

# Страница вычитки на каждый канал: в ленту и в рассылку идут разные тексты,
# и вперемешку их читать нельзя — в ленту публикуется руками, и лишний пост
# уедет в сообщество. Адреса нужны и шаблону (вкладки), и редиректу с кнопки.
# Порядок словаря — порядок вкладок в шаблоне.
CONTENT_PATHS: dict[content.Channel, str] = {
    content.Channel.FEED: "/admin/content",
    content.Channel.EVENING: "/admin/content/evening",
    content.Channel.MAIL: "/admin/content/mail",
}

CONTENT_TITLES: dict[content.Channel, str] = {
    content.Channel.FEED: "Посты в ленту",
    content.Channel.EVENING: "Гороскоп на завтра",
    content.Channel.MAIL: "Гороскопы в рассылку",
}

# Период воронки по умолчанию, дней включая сегодня. Неделя, потому что
# рекламная кампания живёт восемь дней, а когорта последних суток всегда
# недосчитана: пришедший вчера ещё может заплатить завтра.
FUNNEL_DAYS = 7

# Что показываем за период, а не по когорте. Приглашения попали сюда
# вынужденно: у pair_invite_opened нет user_id (экран анонимный), и в
# когорту это событие не встаёт ни при каком запросе.
FUNNEL_EXTRA: dict[str, str] = {
    events.PAIR_INVITE_CREATED: "Выдано приглашений",
    events.PAIR_INVITE_OPENED: "Открыли ссылку",
    events.PAIR_PARTNER_JOINED: "Партнёр ввёл данные",
    events.REPORT_FAILED: "Аварий генерации",
    events.REPORT_DENIED: "Отказов по правилам",
    events.BROADCAST_SENT: "Писем рассылки доставлено",
    events.BROADCAST_FAILED: "Писем не доставлено",
}


@router.get("")
async def index(request: Request, session: SessionDep) -> Response:
    """Таблица всех разборов плюс сводка по версиям промптов."""
    return templates.TemplateResponse(
        request,
        "admin.html",
        {
            "rows": await admin_service.list_report_rows(session, limit=LIST_LIMIT),
            "versions": await admin_service.list_versions(session),
            "charts_count": await admin_service.count_charts(session),
            "limit": LIST_LIMIT,
        },
    )


@router.get("/funnel")
async def funnel(
    request: Request,
    session: SessionDep,
    since: Annotated[str | None, Query()] = None,
    until: Annotated[str | None, Query()] = None,
    testers: Annotated[str | None, Query()] = None,
) -> Response:
    """Воронки по когорте пришедших за период плюс счётчики за него же.

    Даты в адресе — московские и включительные обе: человек, открывающий
    экран, мыслит днями кампании, а не полуинтервалами. Пустое и мусорное
    молча даёт последние FUNNEL_DAYS дней, как и на других экранах админки.
    """
    last = _parse_day(until)
    first = _parse_day(since) if since else last - timedelta(days=FUNNEL_DAYS - 1)
    if first > last:
        # Даты перепутаны местами — показываем период, а не пустой экран:
        # пустая воронка выглядит как «никто не пришёл», а это неправда.
        first, last = last, first

    # Сервис ждёт полуинтервал [since, until), поэтому верхняя граница — это
    # полночь СЛЕДУЮЩИХ суток: иначе последний день периода выпал бы целиком.
    start = datetime.combine(first, time.min, tzinfo=transits.MSK)
    end = datetime.combine(last + timedelta(days=1), time.min, tzinfo=transits.MSK)

    include_testers = testers == "on"
    funnels = await admin_service.build_funnels(
        session, since=start, until=end, include_testers=include_testers
    )
    counts = await admin_service.count_events(
        session, since=start, until=end, names=list(FUNNEL_EXTRA)
    )

    return templates.TemplateResponse(
        request,
        "admin_funnel.html",
        {
            "funnels": funnels,
            "extra": [(title, counts.get(name, 0)) for name, title in FUNNEL_EXTRA.items()],
            # Строками: они же уходят обратно в поля формы как есть.
            "since": first.isoformat(),
            "until": last.isoformat(),
            "include_testers": include_testers,
        },
    )


@router.get("/user")
async def user_search(
    request: Request,
    session: SessionDep,
    q: Annotated[str, Query()] = "",
) -> Response:
    """Поиск человека; ровно одно совпадение сразу открывает его карточку."""
    found = await admin_service.find_users(session, q) if q.strip() else []
    if len(found) == 1:
        return RedirectResponse(f"/admin/user/{found[0].id}", status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request,
        "admin_user.html",
        {"q": q, "found": found, "searched": bool(q.strip()), "card": None},
    )


@router.get("/user/{user_id}")
async def user_card(request: Request, session: SessionDep, user_id: int) -> Response:
    """Карточка человека: профиль, карты, заказы и лента событий."""
    card_ = await admin_service.get_user_card(session, user_id)
    if card_ is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Такого пользователя нет")

    is_member: bool | None = None
    if card_.user.platform == Platform.VK:
        # Живой запрос: вступления и выходы из сообщества в базу не пишутся.
        is_member = await vk_api.is_group_member(int(card_.user.external_id))

    return templates.TemplateResponse(
        request,
        "admin_user.html",
        {
            "q": "",
            "found": [],
            "searched": False,
            "card": card_,
            "is_member": is_member,
        },
    )


@router.post("/users/{user_id}/tester")
async def toggle_tester(
    user_id: int,
    session: SessionDep,
    value: Annotated[str, Form()] = "",
) -> Response:
    """Выдать или снять признак тестера. Возвращает на таблицу разборов.

    Нужное значение приходит формой, а не считается от текущего: в двух
    открытых вкладках админки кнопки-переключатели гасили бы работу друг
    друга. Раньше признак давала секретная ссылка `?t=`, она ушла вместе
    со стендом.
    """
    user = await users_service.get_by_id(session, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Такого пользователя нет")

    wanted = value == "on"
    if users_service.set_tester(user, wanted) and wanted:
        await events.log(session, events.TESTER_ACTIVATED, user_id=user.id)
    await session.commit()

    # Внутренний user_id, а не имя и не vk_id: права меняются редко, и след
    # в логе нужен, а персональных данных в нём быть не должно.
    logger.info("Админка: тестер у пользователя %s → %s", user_id, wanted)
    return RedirectResponse("/admin", status.HTTP_303_SEE_OTHER)


@dataclass(frozen=True, slots=True)
class _VipForm:
    """Поля формы VIP-заказа строками — как пришли, чтобы вернуть их в форму."""

    vk: str = ""
    person_name: str = ""
    gender: str = ""
    birth_date: str = ""
    birth_time: str = ""
    city: str = ""


@router.get("/vip")
async def vip_orders(request: Request, session: SessionDep) -> Response:
    """VIP-заказы и форма нового: данные из лички → карта → ссылка на оплату."""
    return await _vip_response(request, session, _VipForm())


@router.post("/vip")
async def vip_create(
    request: Request,
    session: SessionDep,
    vk: Annotated[str, Form()] = "",
    person_name: Annotated[str, Form()] = "",
    gender: Annotated[str, Form()] = "",
    birth_date: Annotated[str, Form()] = "",
    birth_time: Annotated[str, Form()] = "",
    city: Annotated[str, Form()] = "",
    place_query: Annotated[str, Form()] = "",
    place: Annotated[str, Form()] = "",
) -> Response:
    """Завести VIP-заказ. Город подтверждается вторым заходом формы.

    Первый заход показывает найденные города: неоднозначное место уточняется
    до оплаты, а не после (docs/sales-policy.md, шаг 4). Номер выбранного
    варианта действует, только пока город в поле тот же, по которому искали.
    """
    form = _VipForm(
        vk=vk.strip(),
        person_name=person_name.strip(),
        gender=gender,
        birth_date=birth_date,
        birth_time=birth_time,
        city=city.strip(),
    )

    vk_user_id = await admin_service.resolve_vk_user_id(form.vk)
    if vk_user_id is None:
        return await _vip_response(
            request, session, form, error="Не узнали человека VK: нужна ссылка на профиль, id123 или число"
        )

    # Пол нужен промпту для рода в тексте; required в форме обходится старой вкладкой.
    try:
        client_gender = ClientGender(form.gender)
    except ValueError:
        return await _vip_response(request, session, form, error="Укажите пол клиента")

    places = charts_service.search_places(form.city)
    if not places:
        return await _vip_response(request, session, form, error="Город не нашёлся — проверьте написание")

    chosen = None
    if place.isdigit() and place_query == form.city and int(place) < len(places):
        chosen = places[int(place)]

    # Даты проверяет та же схема, что у приложения; координаты ей нужны любые
    # из найденных — до выбора города проверяются только дата и имя.
    probe = chosen or places[0]
    try:
        payload = ChartIn(
            person_name=form.person_name or None,
            birth_date=form.birth_date,
            birth_time=form.birth_time or None,
            lat=probe.lat,
            lon=probe.lon,
            place_name=probe.label,
        )
    except ValidationError as e:
        problems = "; ".join(str(err["msg"]).removeprefix("Value error, ") for err in e.errors())
        return await _vip_response(request, session, form, error=problems, places=places)

    if chosen is None:
        return await _vip_response(request, session, form, places=places)

    try:
        order = await vip.create_order(
            session,
            vk_user_id=vk_user_id,
            # Полночь — заглушка: при неизвестном времени create_chart ставит полдень.
            birth_local=datetime.combine(payload.birth_date, payload.birth_time or time.min),
            time_known=payload.birth_time is not None,
            # Зона по координатам, как в приложении: иначе его карта того же
            # человека получила бы другой input_hash и задвоилась.
            place=charts_service.place_from_coords(chosen.lat, chosen.lon, chosen.label),
            client_gender=client_gender,
            person_name=payload.person_name,
        )
    except orders.NotForSaleError:
        return await _vip_response(request, session, form, error="VIP-разбора нет в прайсе (orders.PRICES)")
    await session.commit()
    return RedirectResponse(f"/admin/vip/{order.id}", status.HTTP_303_SEE_OTHER)


@router.get("/vip/{order_id}")
async def vip_order(request: Request, session: SessionDep, order_id: int) -> Response:
    """Карточка VIP-заказа: кто, по какой карте, статус, ссылка на оплату и текст."""
    item = await vip.get_order(session, order_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Такого VIP-заказа нет")
    return _vip_order_response(request, item)


# Кнопки карточки VIP-заказа. Поле формы `text` у edit — просьба к DeepSeek,
# у save — сам текст черновика; остальным оно не нужно.
_VIP_ACTIONS = {
    "draft": lambda s, oid, text: vip_draft.make_draft(s, oid),
    "edit": vip_draft.request_edit,
    "accept": lambda s, oid, text: vip_draft.accept_candidate(s, oid),
    "reject": lambda s, oid, text: vip_draft.reject_candidate(s, oid),
    "save": vip_draft.save_draft,
    "deliver": lambda s, oid, text: vip_draft.deliver(s, oid),
}


@router.post("/vip/{order_id}/{action}")
async def vip_order_action(
    request: Request,
    session: SessionDep,
    order_id: int,
    action: str,
    text: Annotated[str, Form()] = "",
) -> Response:
    """Действие с текстом VIP-разбора. Успех — редирект на карточку, ошибка — карточка с ней.

    draft и edit ждут ответа DeepSeek около двух минут прямо в запросе:
    заказов единицы, ждёт один владелец.
    """
    handler = _VIP_ACTIONS.get(action)
    if handler is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Нет такого действия")
    try:
        await handler(session, order_id, text)
    except vip_draft.VipDraftError as e:
        await session.rollback()
        item = await vip.get_order(session, order_id)
        if item is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Такого VIP-заказа нет") from e
        return _vip_order_response(
            request, item, error=str(e), edit_request=text if action == "edit" else ""
        )
    await session.commit()
    # 303 и GET: F5 на карточке не повторит POST и второй вызов DeepSeek.
    return RedirectResponse(f"/admin/vip/{order_id}", status.HTTP_303_SEE_OTHER)


def _vip_order_response(
    request: Request, item: vip.VipOrder, *, error: str | None = None, edit_request: str = ""
) -> Response:
    """Карточка VIP-заказа с находками проверки фактов по черновику и варианту."""
    order = item.order
    return templates.TemplateResponse(
        request,
        "admin_vip_order.html",
        {
            "item": item,
            "pay_url": vip.payment_url(order),
            "price": orders.price_rub(ReportType.VIP_NATAL),
            "delivery_hours": int(vip.DELIVERY_TIME.total_seconds() // 3600),
            "error": error,
            "edit_request": edit_request,
            "draft_findings": vip_draft.check_draft(item),
            "candidate_findings": (
                vip_draft.check_draft(item, order.draft_candidate) if order.draft_candidate else []
            ),
        },
    )


async def _vip_response(
    request: Request,
    session: AsyncSession,
    form: _VipForm,
    *,
    error: str | None = None,
    places: list[charts_service.Place] | None = None,
) -> Response:
    """Экран VIP-заказов: список, форма с введённым и, если есть, выбор города."""
    return templates.TemplateResponse(
        request,
        "admin_vip.html",
        {
            "items": await vip.list_orders(session),
            "form": form,
            "error": error,
            "places": places or [],
            # Для пометки «просрочен» у заказов в работе.
            "now": datetime.now(UTC),
        },
    )


@router.get("/compare")
async def compare(
    request: Request,
    session: SessionDep,
    ids: Annotated[list[str] | None, Query()] = None,
) -> Response:
    """Тексты выбранных разборов рядом колонками.

    Оба написания `ids` понимаются одинаково: `?ids=12&ids=15` шлёт форма с
    чекбоксами, `?ids=12,15` удобнее править руками. Мусор в параметре молча
    отбрасывается — ошибка ввода не должна ронять страницу.
    """
    report_ids = _parse_ids(ids)
    items = await admin_service.get_reports_for_compare(session, report_ids)

    return templates.TemplateResponse(
        request,
        "admin_compare.html",
        {
            "items": [
                # Секции считаем здесь: шаблон не должен знать, что full_natal
                # приходит JSON'ом, а free_natal — сплошным текстом.
                {"item": item, "sections": reports.sections(item.report)}
                for item in items
            ],
            "missing": len(report_ids) - len(items),
        },
    )


@router.get("/content")
async def content_feed_page(request: Request, session: SessionDep) -> Response:
    """Неделя постов в ленту сообщества: обзор неба суток и события календаря."""
    return await _content_response(request, session, content.Channel.FEED)


@router.get("/content/evening")
async def content_evening_page(request: Request, session: SessionDep) -> Response:
    """Неделя вечерних постов «Гороскоп на завтра» — своя очередь публикации."""
    return await _content_response(request, session, content.Channel.EVENING)


@router.get("/content/mail")
async def content_mail_page(request: Request, session: SessionDep) -> Response:
    """Неделя гороскопов для личной рассылки — вычитка вперёд.

    Те же тексты потом разбирает по знакам /admin/broadcast, но там виден
    только сегодняшний день и стоит кнопка отправки.
    """
    return await _content_response(request, session, content.Channel.MAIL)


async def _content_response(
    request: Request, session: SessionDep, channel: content.Channel
) -> Response:
    """Одна страница вычитки на все каналы: список постов и кнопка прогона.

    Только читает: прогон длится минут десять и держал бы ответ всё это
    время — тем же порядком, что на странице разбора.
    """
    run = generation.content_state()
    posts = await content.list_week(session, days=CONTENT_DAYS, channel=channel)

    return templates.TemplateResponse(
        request,
        "admin_content.html",
        {
            "items": [
                # Текст готовим здесь: шаблон не должен знать, что ответ
                # модели лежит в базе JSON'ом и разбирает его модуль промпта.
                # Длина в словах — для калибровки промпта (рамки заданы в его
                # SYSTEM), поэтому она рядом с текстом, а не внутри него.
                {
                    "post": post,
                    "text": text,
                    "words": len(text.split()),
                    # Вечер публикации для sign_lines: он пишется на завтра.
                    "evening_before": post.target_date - timedelta(days=1),
                }
                for post, text in ((p, content.render(p)) for p in posts)
            ],
            "run": run,
            "start": content.today_msk(),
            "days": CONTENT_DAYS,
            # Канал в контексте — чтобы шаблон подсветил активную вкладку и
            # вернул кнопку прогона на эту же страницу.
            "channel": channel.value,
            "title": CONTENT_TITLES[channel],
            "tabs": [
                (c.value, CONTENT_PATHS[c], CONTENT_TITLES[c]) for c in CONTENT_PATHS
            ],
            # Пока прогон идёт, страница перечитывает себя сама; как только
            # он кончился — перестаёт, иначе экран сам себе создаёт поток
            # запросов на всю ночь.
            "refresh": CONTENT_REFRESH_SECONDS if run is not None and run.running else None,
        },
    )


@router.get("/content/card/{post_id}.png")
async def content_card(post_id: int, session: SessionDep) -> Response:
    """Картинка к посту в ленту: её прикладывают к тексту в отложке VK руками.

    Ключ — пост, а не дата: «Стоит / Подождёт» берутся из его текста, а
    после смены VERSION у одного дня два поста с разными концовками.
    """
    parts = await content.card_parts(session, post_id)
    if parts is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return Response(card.render(*parts), media_type="image/png")


@router.post("/content/generate")
async def content_generate(
    background: BackgroundTasks,
    channel: Annotated[str | None, Form()] = None,
) -> Response:
    """Кнопка «сгенерировать неделю»: ставит прогон в очередь и уводит обратно.

    Прогон общий на все каналы, поэтому кнопка одна на все страницы, а
    `channel` из формы говорит только, куда вернуть человека.

    Отказ замка (прогон уже идёт) отдельно не показываем: страница и так
    напишет, что прогон идёт с такого-то времени.
    """
    if not generation.schedule_content(background):
        logger.info("Прогон контента уже идёт — второй запуск отклонён")

    # 303, а не 302: браузер обязан перейти GET'ом, иначе «обновить» на
    # странице переотправит форму и попробует запустить прогон снова.
    # Токен сессии не дописываем — в админку из VK не ходят, там Basic.
    return RedirectResponse(_content_path(channel), status_code=status.HTTP_303_SEE_OTHER)


def _content_path(raw: str | None) -> str:
    """«mail» → адрес страницы рассылки; пусто или мусор → страница ленты.

    Мусор молча уводит на ленту, как и в _parse_day: опечатка в форме не
    должна ронять ответ на кнопку, которая уже потратила деньги.
    """
    try:
        return CONTENT_PATHS[content.Channel(raw)]
    except ValueError:
        return CONTENT_PATHS[content.Channel.FEED]


@router.get("/broadcast")
async def broadcast_page(
    request: Request,
    session: SessionDep,
    day: Annotated[str | None, Query()] = None,
) -> Response:
    """Экран рассылки: охват, двенадцать писем на вычитку и кнопка отправки."""
    return _broadcast_response(request, await broadcast.preview(session, _parse_day(day)))


@router.post("/broadcast/send")
async def broadcast_send(
    request: Request,
    session: SessionDep,
    day: Annotated[str | None, Form()] = None,
) -> Response:
    """Разослать гороскоп и показать итог тем же экраном.

    Ответ страницей, а не редиректом (в отличие от кнопки контента): итог
    рассылки — единственное место, где видны отказы по кодам, и после 303 он
    бы потерялся. Повторная отправка по F5 безвредна: за этот день письмо уже
    отмечено в events, и второй раз оно никому не уйдёт.
    """
    target_date = _parse_day(day)
    try:
        report = await broadcast.run(session, target_date)
    except LookupError as e:
        # Поста за день нет или он не разобрался — это состояние экрана,
        # а не сбой: причину покажет сам preview.
        logger.info("Рассылка за %s не запущена: %s", target_date, e)
        return _broadcast_response(request, await broadcast.preview(session, target_date))

    # Коммит здесь, а не в сервисе: письма уже ушли, и события об этом
    # обязаны пережить ответ — иначе следующий запуск отправит их снова.
    await session.commit()
    return _broadcast_response(request, await broadcast.preview(session, target_date), report)


def _broadcast_response(
    request: Request,
    preview: broadcast.Preview,
    report: broadcast.BroadcastReport | None = None,
) -> Response:
    """Один шаблон на обе ручки: GET показывает его без отчёта, POST — с ним."""
    return templates.TemplateResponse(
        request,
        "admin_broadcast.html",
        {"preview": preview, "report": report},
    )


def _parse_day(raw: str | None) -> date:
    """«2026-08-25» → дата; пусто или мусор → сегодня по Москве.

    Мусор молча превращается в сегодня, как и в _parse_ids: опечатка в
    адресе не должна ронять экран.
    """
    try:
        return date.fromisoformat(raw) if raw else content.today_msk()
    except ValueError:
        return content.today_msk()


def _parse_ids(raw: list[str] | None) -> list[int]:
    """[«12,15», «7»] → [12, 15, 7]; нечисловое и повторы выброшены.

    Порядок первого появления сохраняется — он и задаёт порядок колонок,
    а сервис его не меняет.
    """
    seen: dict[int, None] = {}
    for chunk in raw or []:
        for part in chunk.split(","):
            part = part.strip()
            if part.isdigit():
                seen.setdefault(int(part), None)
    return list(seen)[:MAX_COMPARE]
