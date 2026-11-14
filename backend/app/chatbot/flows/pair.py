"""ПАРА: свои данные → партнёр → бесплатный тизер → заказ разбора пары.

Свои данные берём из data.last или собираем потоком КАРТЫ (purpose="pair"),
партнёра спрашиваем сами: дата обязательна, город — только при известном
времени. Тизер (вызов LLM) идёт отдельной работой Job. Прямой вход с экрана
VIP (direct) идёт мимо тизера. Всё состояние потока — в data["pair"].
Заказ заводит services/vip_pair; оплата только по внешней ссылке на сайт.
Устройство и события — docs/vk-bot-plan.md, раздел ПАРА.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.chatbot import parse, states, storage, texts
from app.chatbot.flows import natal, vip
from app.chatbot.types import Button, Incoming, Job, LinkButton, Outgoing, Reply
from app.models import BotDialog, OrderStatus, ReportType, User
from app.services import charts as charts_service
from app.services import events, orders, pair_teaser, vip_pair
from app.services import vip as vip_service

logger = logging.getLogger(__name__)

JOB_TEASER = "pair_teaser"

# Откуда человек вошёл в ПАРУ — значения ref по умолчанию (иначе метка из vk.me/…?ref=).
REF_MENU = "pair_menu"
REF_POST = "pair_post"
REF_DM = "pair_dm"
REF_VIP = "pair_vip"

_KEY = "pair"
_ASK_DATE, _ASK_TIME, _ASK_CITY, _ASK_NAME = "date", "time", "city", "name"
_YES = frozenset({"да", "ага", "верно", "все верно", "ок", "+"})

# Тизер завис дольше этого (перезапуск процесса посреди Job) — предлагаем повторить.
_WAIT_STALE = timedelta(minutes=5)


def _price() -> int:
    return orders.price_rub(ReportType.VIP_PAIR)


def _for_sale() -> bool:
    return orders.is_for_sale(ReportType.VIP_PAIR)


def _has_birth(value: Any) -> bool:
    return isinstance(value, dict) and {"date", "time_known", "place"} <= value.keys()


def _pair(data: dict[str, Any]) -> dict[str, Any] | None:
    """Копия data["pair"]; None — потока нет (старая кнопка после сброса)."""
    value = data.get(_KEY)
    return dict(value) if isinstance(value, dict) else None


def _ready(pair: dict[str, Any]) -> bool:
    """Собраны обе стороны: свои данные и партнёр целиком."""
    partner = pair.get("partner") or {}
    return _has_birth(pair.get("self")) and _next_ask(partner) is None


def _next_ask(partner: dict[str, Any]) -> str | None:
    """Первое, чего у партнёра не хватает; None — всё есть."""
    if "date" not in partner:
        return _ASK_DATE
    if "time_known" not in partner:
        return _ASK_TIME
    # Город нужен только при известном времени: без него считаем в городе клиента.
    if partner["time_known"] and "place" not in partner:
        return _ASK_CITY
    if "name" not in partner:
        return _ASK_NAME
    return None


async def _save(
    session: AsyncSession, dialog: BotDialog, pair: dict[str, Any], state: str
) -> None:
    """Записать поток, не трогая остальное в data (last переживает всё)."""
    data = dict(dialog.data or {})
    data[_KEY] = pair
    await storage.save(session, dialog, state=state, data=data)


# --- вход ----------------------------------------------------------------------


async def start(
    session: AsyncSession,
    user: User,
    dialog: BotDialog,
    *,
    ref: str,
    direct: bool = False,
) -> list[Outgoing]:
    """Начать ПАРУ. direct — вход с экрана VIP: без тизера, сразу к заказу."""
    if direct and not _for_sale():
        return [Reply(text=texts.PAIR_NOT_FOR_SALE)]
    await events.log(session, events.BOT_PAIR_STARTED, user_id=user.id, ref=ref)
    return await _ask_self(session, user, dialog, {"ref": ref[:64], "direct": direct})


async def _ask_self(
    session: AsyncSession, user: User, dialog: BotDialog, pair: dict[str, Any]
) -> list[Outgoing]:
    """Свои данные: подтвердить прошлые или собрать потоком КАРТЫ."""
    pair = {"ref": pair.get("ref"), "direct": bool(pair.get("direct"))}
    last = (dialog.data or {}).get("last")
    if not _has_birth(last):
        return await _collect_self(session, user, dialog, pair)
    await storage.save(
        session, dialog, state=states.PAIR_SELF, data={"last": last, _KEY: pair}
    )
    return [
        Reply(
            text=texts.PAIR_CONFIRM_SELF.format(summary=natal.summary(last)),
            buttons=[
                [
                    Button(texts.BTN_YES, {"cmd": "pair_self_ok"}, color="positive"),
                    Button(texts.BTN_OTHER_DATA, {"cmd": "pair_self_other"}),
                ]
            ],
            inline=True,
        )
    ]


async def _collect_self(
    session: AsyncSession,
    user: User,
    dialog: BotDialog,
    pair: dict[str, Any],
    *,
    first_text: str | None = None,
) -> list[Outgoing]:
    keep: dict[str, Any] = {_KEY: pair}
    last = (dialog.data or {}).get("last")
    if last is not None:
        keep["last"] = last
    return await natal.start(
        session, user, dialog,
        purpose=natal.PURPOSE_PAIR, keep=keep,
        ask_text=texts.PAIR_ASK_SELF, first_text=first_text,
    )


async def after_self_data(
    session: AsyncSession,
    user: User,
    dialog: BotDialog,
    data: dict[str, Any],
) -> list[Outgoing]:
    """Свои данные собраны потоком КАРТЫ — переходим к партнёру (зовёт natal)."""
    pair = _pair(data) or {"ref": REF_DM, "direct": False}
    pair["self"] = natal.birth_only(data)
    # Черновик сбора (date, time, place на верхнем уровне) дальше не нужен;
    # свои данные сразу кладём под last — «Исправить» предложит их же.
    await storage.save(
        session, dialog, state=states.PAIR_PARTNER, data={"last": pair["self"], _KEY: pair}
    )
    return await _ask_partner(session, dialog, pair)


# --- кнопки и текст ------------------------------------------------------------


async def dispatch(
    session: AsyncSession,
    user: User,
    dialog: BotDialog,
    payload: dict[str, Any],
) -> list[Outgoing] | None:
    """Кнопки ПАРЫ; None — команда не отсюда."""
    cmd = str(payload.get("cmd", ""))
    if cmd == "pair_vip":
        return await start(session, user, dialog, ref=REF_VIP, direct=True)
    if not cmd.startswith("pair_"):
        return None

    data = dict(dialog.data or {})
    pair = _pair(data)
    if pair is None:
        return [Reply(text=texts.PAIR_EXPIRED)]
    partner = dict(pair.get("partner") or {})

    if cmd == "pair_self_ok" and _has_birth(data.get("last")):
        pair["self"] = natal.birth_only(data["last"])
        return await _ask_partner(session, dialog, pair)
    if cmd == "pair_self_other":
        return await _collect_self(session, user, dialog, pair)
    if cmd == "pair_time_unknown":
        partner["time_known"] = False
        partner.pop("time", None)
        return await _ask_partner(session, dialog, {**pair, "partner": partner})
    if cmd == "pair_place":
        places = partner.get("places") or []
        index = payload.get("i")
        if isinstance(index, int) and 0 <= index < len(places):
            partner["place"] = places[index]
        partner.pop("places", None)
        return await _ask_partner(session, dialog, {**pair, "partner": partner})
    if cmd == "pair_city_again":
        partner.pop("place", None)
        partner.pop("places", None)
        return await _ask_partner(session, dialog, {**pair, "partner": partner})
    if cmd == "pair_skip_name":
        partner["name"] = None
        return await _partner_done(session, user, dialog, {**pair, "partner": partner})

    # Дальше — кнопки после сбора: без обеих сторон они смысла не имеют.
    if not _ready(pair):
        return [Reply(text=texts.PAIR_EXPIRED)]
    if cmd == "pair_retry":
        return await _launch_teaser(session, dialog, pair)
    if cmd == "pair_full":
        return await _offer(session, dialog, pair)
    if cmd == "pair_go":
        return await _summary(session, dialog, pair)
    if cmd == "pair_fix":
        # Заход на исправление — не новый вход: pair_started второй раз не пишем.
        return await _ask_self(session, user, dialog, pair)
    if cmd == "pair_confirm" and dialog.state == states.PAIR_ORDER:
        return await _create(session, user, dialog, pair)
    return None


async def handle_text(
    session: AsyncSession,
    user: User,
    dialog: BotDialog,
    incoming: Incoming,
) -> list[Outgoing]:
    """Текст в состояниях pair_*."""
    pair = _pair(dict(dialog.data or {}))
    if pair is None:
        await storage.save(session, dialog, state=states.IDLE)
        return [Reply(text=texts.PAIR_EXPIRED)]

    if dialog.state == states.PAIR_SELF:
        if " ".join(incoming.text.lower().replace("ё", "е").split()) in _YES:
            return await dispatch(session, user, dialog, {"cmd": "pair_self_ok"}) or []
        # Вместо кнопки человек сразу прислал другие данные.
        if natal.looks_like_birth_data(incoming.text):
            return await _collect_self(session, user, dialog, pair, first_text=incoming.text)
        return await _ask_self(session, user, dialog, pair)
    if dialog.state == states.PAIR_PARTNER:
        return await _absorb_partner(session, user, dialog, pair, incoming.text)
    if dialog.state == states.PAIR_TEASER:
        if _wait_is_stale(dialog):
            return [_failed_reply()]
        return [Reply(text=texts.PAIR_WAITING)]
    return await _summary(session, dialog, pair)


# --- партнёр -------------------------------------------------------------------


async def _absorb_partner(
    session: AsyncSession,
    user: User,
    dialog: BotDialog,
    pair: dict[str, Any],
    text: str,
) -> list[Outgoing]:
    """Влить в черновик партнёра всё, что нашлось в тексте, и спросить недостающее."""
    partner = dict(pair.get("partner") or {})
    ask = _next_ask(partner)

    if ask == _ASK_NAME:
        # Имя не разбираем как данные рождения: «Маша» иначе ушла бы в поиск города.
        partner["name"] = vip.clean_name(text) or None
        return await _partner_done(session, user, dialog, {**pair, "partner": partner})

    guess = parse.parse_birth(text, expect=ask)
    if guess.bad_date:
        return [Reply(text=texts.NATAL_BAD_DATE)]
    # Даты ещё нет и в сообщении её нет — ничего не берём: «не помню» на вопрос
    # о дате иначе записалось бы как «время неизвестно».
    if ask == _ASK_DATE and guess.birth_date is None:
        return [Reply(text=texts.PAIR_DATE_NOT_UNDERSTOOD)]
    if guess.birth_date is not None:
        partner["date"] = guess.birth_date.isoformat()
    if guess.time_unknown:
        partner["time_known"] = False
        partner.pop("time", None)
    elif guess.birth_time is not None:
        partner["time_known"] = True
        partner["time"] = guess.birth_time.strftime("%H:%M")

    got_birth = guess.birth_date is not None or guess.birth_time is not None or guess.time_unknown
    # Город берём, когда его спрашивали или он пришёл вместе с датой/временем:
    # одинокое слово на вопрос о дате — не город.
    if guess.city and (ask == _ASK_CITY or got_birth):
        found = parse.find_places(guess.city)
        if found.places and found.sure:
            partner["place"] = _place_dict(found.places[0])
            partner.pop("places", None)
        elif found.places:
            partner["places"] = [_place_dict(p) for p in found.places]
            partner.pop("place", None)
        elif ask == _ASK_CITY:
            await _save(session, dialog, {**pair, "partner": partner}, states.PAIR_PARTNER)
            return [Reply(text=texts.NATAL_CITY_NOT_FOUND)]
    elif not got_birth:
        return [Reply(text=_not_understood(ask))]
    return await _ask_partner(session, dialog, {**pair, "partner": partner})


async def _ask_partner(
    session: AsyncSession, dialog: BotDialog, pair: dict[str, Any]
) -> list[Outgoing]:
    """Спросить первое недостающее у партнёра. Зовётся, пока _next_ask не None."""
    partner = dict(pair.get("partner") or {})
    pair = {**pair, "partner": partner}
    ask = _next_ask(partner)
    await _save(session, dialog, pair, states.PAIR_PARTNER)

    if ask == _ASK_DATE:
        return [Reply(text=texts.PAIR_ASK_PARTNER)]
    if ask == _ASK_TIME:
        return [
            Reply(
                text=texts.PAIR_ASK_PARTNER_TIME.format(when=natal.human_date(partner["date"])),
                buttons=[[Button(texts.BTN_TIME_UNKNOWN, {"cmd": "pair_time_unknown"})]],
                inline=True,
            )
        ]
    if ask == _ASK_CITY:
        places = partner.get("places")
        if not places:
            return [Reply(text=texts.PAIR_ASK_PARTNER_CITY)]
        rows = [[Button(p["label"], {"cmd": "pair_place", "i": i})] for i, p in enumerate(places)]
        rows.append([Button(texts.BTN_OTHER_CITY, {"cmd": "pair_city_again"})])
        return [Reply(text=texts.PAIR_PICK_CITY, buttons=rows, inline=True)]
    # Имя — последний вопрос; _partner_done сюда уже не приходит.
    return [
        Reply(
            text=texts.PAIR_ASK_NAME,
            buttons=[[Button(texts.BTN_SKIP, {"cmd": "pair_skip_name"})]],
            inline=True,
        )
    ]


async def _partner_done(
    session: AsyncSession, user: User, dialog: BotDialog, pair: dict[str, Any]
) -> list[Outgoing]:
    """Партнёр собран: к тизеру, а при прямом входе — сразу к описанию с ценой."""
    partner = pair["partner"]
    await events.log(
        session, events.BOT_PAIR_PARTNER_GIVEN,
        user_id=user.id, ref=pair.get("ref"), time_known=bool(partner.get("time_known")),
    )
    if pair.get("direct"):
        return await _offer(session, dialog, pair)
    return await _launch_teaser(session, dialog, pair)


def _not_understood(ask: str | None) -> str:
    if ask == _ASK_TIME:
        return texts.NATAL_TIME_NOT_UNDERSTOOD
    if ask == _ASK_CITY:
        return texts.NATAL_CITY_NOT_FOUND
    return texts.PAIR_DATE_NOT_UNDERSTOOD


def _place_dict(place: charts_service.Place) -> dict[str, Any]:
    return {"label": place.label, "lat": place.lat, "lon": place.lon, "tz": place.tz}


def _partner(partner: dict[str, Any]) -> vip_pair.Partner:
    """Черновик партнёра из data → аргумент vip_pair."""
    known = bool(partner.get("time_known"))
    p = partner.get("place") if known else None
    return vip_pair.Partner(
        birth_date=date.fromisoformat(partner["date"]),
        birth_time=time.fromisoformat(partner["time"]) if known else None,
        place=charts_service.Place(label=p["label"], lat=p["lat"], lon=p["lon"], tz=p["tz"])
        if p
        else None,
        name=partner.get("name"),
    )


def partner_summary(partner: dict[str, Any]) -> str:
    """«Маша, 11 мая 1990, время неизвестно» — для сверки человеком."""
    parts = [natal.human_date(partner["date"])]
    if partner.get("time_known"):
        parts += [partner["time"], partner["place"]["label"]]
    else:
        parts.append(texts.TIME_UNKNOWN_LABEL)
    if partner.get("name"):
        parts.insert(0, partner["name"])
    return ", ".join(parts)


# --- тизер ---------------------------------------------------------------------


async def _launch_teaser(
    session: AsyncSession, dialog: BotDialog, pair: dict[str, Any]
) -> list[Outgoing]:
    await _save(session, dialog, pair, states.PAIR_TEASER)
    return [Reply(text=texts.PAIR_COUNTING), Job(JOB_TEASER)]


def _wait_is_stale(dialog: BotDialog) -> bool:
    updated = getattr(dialog, "updated_at", None)
    return updated is not None and datetime.now(UTC) - updated > _WAIT_STALE


def _failed_reply() -> Reply:
    return Reply(
        text=texts.PAIR_FAILED,
        buttons=[[Button(texts.BTN_RETRY, {"cmd": "pair_retry"})]],
        inline=True,
    )


async def run_teaser(
    session: AsyncSession, user: User, dialog: BotDialog
) -> list[Outgoing]:
    """Job: карта клиента → синастрия → три строки и картинка. Коммитит сам.

    Потолок и авария превращаются в ответ; данные пары в data остаются.
    """
    pair = _pair(dict(dialog.data or {}))
    if dialog.state != states.PAIR_TEASER or pair is None or not _ready(pair):
        return []

    birth_local, time_known, place = natal.birth_values(pair["self"])
    chart = await charts_service.create_chart(
        session, user_id=user.id, birth_local=birth_local, place=place, time_known=time_known,
    )
    # Карту фиксируем до LLM: падение генерации её не откатит.
    await session.commit()
    # rollback() ниже протухает объекты сессии — id берём заранее (как в natal.run_report).
    user_id, chart_id, ref = user.id, chart.id, pair.get("ref")

    try:
        pair_data = await vip_pair.partner_data(
            _partner(pair["partner"]), client_place=place, ref=ref
        )
        teaser = await pair_teaser.make(session, chart, pair_data)
    except pair_teaser.TeaserBudgetError:
        await session.rollback()
        await _save(session, dialog, pair, states.IDLE)
        await session.commit()
        return [Reply(text=texts.PAIR_LIMIT)]
    except Exception as error:  # noqa: BLE001 — TeaserError и прочая авария → «Повторить»
        await session.rollback()
        logger.error(
            "chatbot.pair teaser failed user_id=%s chart_id=%s error_type=%s",
            user_id, chart_id, type(error).__name__,
        )
        await _save(session, dialog, pair, states.IDLE)
        await session.commit()
        return [_failed_reply()]

    await events.log(session, events.BOT_PAIR_TEASER_SHOWN, user_id=user_id, ref=ref)
    # Свои данные кладём под last: следующий сценарий не спросит их снова.
    data = dict(dialog.data or {})
    data["last"] = pair["self"]
    await storage.save(session, dialog, state=states.IDLE, data=data)
    await session.commit()

    buttons: list[list[Button]] = []
    if _for_sale():
        buttons.append([Button(texts.BTN_PAIR_FULL, {"cmd": "pair_full"}, color="positive")])
    return [
        Reply(
            text=f"{texts.PAIR_TEASER_HEADER}\n\n{teaser.text}",
            buttons=buttons,
            inline=True,
            image=teaser.image,
        )
    ]


# --- заказ ---------------------------------------------------------------------


async def _offer(
    session: AsyncSession, dialog: BotDialog, pair: dict[str, Any]
) -> list[Outgoing]:
    """Описание платного разбора с ценой и кнопкой «Оформить»."""
    await _save(session, dialog, pair, states.IDLE)
    if not _for_sale():
        return [Reply(text=texts.PAIR_NOT_FOR_SALE)]
    price = _price()
    return [
        Reply(
            text=texts.PAIR_INTRO.format(price=price),
            buttons=[
                [Button(texts.BTN_VIP_GO.format(price=price), {"cmd": "pair_go"}, color="positive")],
                [Button(texts.BTN_MENU, {"cmd": "menu"})],
            ],
            inline=True,
        )
    ]


async def _summary(
    session: AsyncSession, dialog: BotDialog, pair: dict[str, Any]
) -> list[Outgoing]:
    """Сводка обеих сторон перед заказом."""
    await _save(session, dialog, pair, states.PAIR_ORDER)
    partner = pair["partner"]
    text = texts.PAIR_CONFIRM.format(
        me=natal.summary(pair["self"]), partner=partner_summary(partner)
    )
    if not partner.get("time_known"):
        text += "\n\n" + texts.PAIR_NO_TIME_NOTE
    return [
        Reply(
            text=text,
            buttons=[
                [
                    Button(texts.BTN_CONFIRM, {"cmd": "pair_confirm"}, color="positive"),
                    Button(texts.BTN_FIX, {"cmd": "pair_fix"}),
                ]
            ],
            inline=True,
        )
    ]


async def _create(
    session: AsyncSession, user: User, dialog: BotDialog, pair: dict[str, Any]
) -> list[Outgoing]:
    birth_local, time_known, place = natal.birth_values(pair["self"])
    ref = pair.get("ref")
    try:
        order = await vip_pair.create_order(
            session,
            user=user,
            birth_local=birth_local,
            birth_time_known=time_known,
            place=place,
            partner=_partner(pair["partner"]),
            ref=ref,
        )
    except orders.NotForSaleError:
        await storage.save(session, dialog, state=states.IDLE)
        return [Reply(text=texts.PAIR_NOT_FOR_SALE)]

    await events.log(
        session, events.BOT_PAIR_ORDER_CREATED, user_id=user.id, ref=ref, order_id=order.id
    )
    # Партнёр теперь лежит в заказе; в диалоге оставляем только свои данные.
    await storage.save(session, dialog, state=states.IDLE, data={"last": pair["self"]})
    if order.status is OrderStatus.PAID:
        logger.info("chatbot.pair already paid user_id=%s order_id=%s", user.id, order.id)
        return [Reply(text=texts.PAIR_ALREADY_PAID)]
    url = vip_service.payment_url(order)
    if url is None:
        # Владелец увидит заказ в /admin/vip и напишет сам; в лог — только id.
        logger.warning("chatbot.pair payment unavailable user_id=%s order_id=%s", user.id, order.id)
        return [Reply(text=texts.VIP_PAY_UNAVAILABLE)]
    return [
        Reply(
            text=texts.PAIR_PAY,
            buttons=[[LinkButton(texts.BTN_PAY.format(price=order.amount // 100), url)]],
            inline=True,
        )
    ]
