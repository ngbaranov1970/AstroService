"""КАРТА: сбор данных рождения в свободной форме и бесплатный разбор в личке.

Порядок вопросов не жёсткий: каждое сообщение разбирается целиком
(chatbot/parse.py), спрашиваем только то, чего ещё нет. Тот же сбор
переиспользуют VIP (purpose="vip") и ПАРА (purpose="pair") — после него
управление у flows/vip.py и flows/pair.py.
Разбор (30–60 с) идёт отдельной работой Job после ответа «считаю».
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.chatbot import parse, states, storage, texts
from app.chatbot.types import Button, Incoming, Job, Outgoing, Reply
from app.models import BotDialog, ReportType, User
from app.services import charts as charts_service
from app.services import events, orders, reports

logger = logging.getLogger(__name__)

JOB_REPORT = "natal_report"
PURPOSE_FREE = "free"
PURPOSE_VIP = "vip"
PURPOSE_PAIR = "pair"

# Разметка markdown в тексте разбора: решётки заголовков и звёздочки жирного.
# Личка VK её не рисует — «## Заголовок» дошёл бы до человека с решётками.
_MARKDOWN = re.compile(r"^#{1,4}\s+|\*\*", re.MULTILINE)

# Разбор завис дольше этого (перезапуск процесса посреди Job) — даём начать заново.
_WAIT_STALE = timedelta(minutes=5)
_MONTHS_GEN = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)
_ASKED = {
    states.NATAL_DATE: "date",
    states.NATAL_TIME: "time",
    states.NATAL_CITY: "city",
    states.NATAL_CITY_PICK: "city",
}


def is_collecting(state: str) -> bool:
    return state in _ASKED


def looks_like_birth_data(text: str) -> bool:
    """В idle: сообщение с датой рождения — это КАРТА без кодового слова."""
    return parse.parse_birth(text).birth_date is not None


async def start(
    session: AsyncSession,
    user: User,
    dialog: BotDialog,
    *,
    purpose: str = PURPOSE_FREE,
    first_text: str | None = None,
    keep: dict[str, Any] | None = None,
    ask_text: str = texts.NATAL_ASK_ALL,
) -> list[Outgoing]:
    """Начать сбор. first_text — сообщение, которое уже содержит данные.

    keep — ключи data, которые сбор не должен стереть (ПАРА несёт так свой
    поток и last); ask_text — первый вопрос, у ПАРЫ он свой.
    """
    if dialog.state == states.NATAL_WAIT and not _wait_is_stale(dialog):
        return [Reply(text=texts.NATAL_ALREADY_WAITING)]

    await storage.save(
        session, dialog, state=states.NATAL_DATE, data={**(keep or {}), "purpose": purpose}
    )
    if purpose == PURPOSE_FREE:
        await events.log(session, events.BOT_NATAL_STARTED, user_id=user.id)
    if first_text:
        return await _absorb(session, user, dialog, first_text)
    return [Reply(text=ask_text)]


async def handle_text(
    session: AsyncSession,
    user: User,
    dialog: BotDialog,
    incoming: Incoming,
) -> list[Outgoing]:
    """Текст в состояниях natal_*; natal_wait отвечает «уже считаю»."""
    if dialog.state == states.NATAL_WAIT:
        if _wait_is_stale(dialog):
            return await start(session, user, dialog, first_text=incoming.text)
        return [Reply(text=texts.NATAL_ALREADY_WAITING)]
    return await _absorb(session, user, dialog, incoming.text)


async def dispatch(
    session: AsyncSession,
    user: User,
    dialog: BotDialog,
    payload: dict[str, Any],
) -> list[Outgoing] | None:
    """Кнопки сценария; None — команда не отсюда."""
    cmd = payload.get("cmd")
    data = dict(dialog.data or {})
    if cmd == "natal_time_unknown":
        data["time_known"] = False
        data.pop("time", None)
        return await _next_step(session, user, dialog, data)
    if cmd == "natal_place":
        places = data.get("places") or []
        index = payload.get("i")
        if not isinstance(index, int) or not 0 <= index < len(places):
            return await _ask(session, dialog, data, states.NATAL_CITY)
        data["place"] = places[index]
        data.pop("places", None)
        return await _next_step(session, user, dialog, data)
    if cmd == "natal_city_again":
        data.pop("place", None)
        data.pop("places", None)
        return await _ask(session, dialog, data, states.NATAL_CITY)
    if cmd == "natal_retry":
        if not _complete(data):
            return await start(session, user, dialog)
        return await _launch(session, dialog, data)
    return None


async def _absorb(
    session: AsyncSession,
    user: User,
    dialog: BotDialog,
    text: str,
) -> list[Outgoing]:
    """Влить в черновик всё, что нашлось в тексте, и спросить недостающее."""
    data = dict(dialog.data or {})
    guess = parse.parse_birth(text, expect=_ASKED.get(dialog.state))
    replies: list[Outgoing] = []

    if guess.bad_date:
        return [Reply(text=texts.NATAL_BAD_DATE)]
    if guess.birth_date is not None:
        data["date"] = guess.birth_date.isoformat()
    if guess.time_unknown:
        data["time_known"] = False
        data.pop("time", None)
    elif guess.birth_time is not None:
        data["time_known"] = True
        data["time"] = guess.birth_time.strftime("%H:%M")

    if guess.city:
        found = parse.find_places(guess.city)
        if not found.places:
            # Дату/время из этого же сообщения не теряем — сохраняем и просим город.
            await storage.save(session, dialog, state=states.NATAL_CITY, data=data)
            return [Reply(text=texts.NATAL_CITY_NOT_FOUND)]
        if found.sure:
            data["place"] = _place_dict(found.places[0])
            data.pop("places", None)
        else:
            data["places"] = [_place_dict(p) for p in found.places]
            data.pop("place", None)
            await storage.save(
                session, dialog, state=states.NATAL_CITY_PICK, data=data
            )
            return [_pick_reply(data["places"])]

    nothing_new = (
        guess.birth_date is None
        and guess.birth_time is None
        and not guess.time_unknown
        and not guess.city
    )
    if nothing_new:
        replies.append(Reply(text=_not_understood(dialog.state)))
    return replies + await _next_step(session, user, dialog, data, quiet=bool(replies))


async def _next_step(
    session: AsyncSession,
    user: User,
    dialog: BotDialog,
    data: dict[str, Any],
    *,
    quiet: bool = False,
) -> list[Outgoing]:
    """Спросить первое недостающее; всё есть — к разбору, к VIP или к ПАРЕ."""
    if "date" not in data:
        return await _ask(session, dialog, data, states.NATAL_DATE, quiet=quiet)
    if "time_known" not in data:
        return await _ask(session, dialog, data, states.NATAL_TIME, quiet=quiet)
    if "place" not in data:
        if data.get("places"):
            await storage.save(
                session, dialog, state=states.NATAL_CITY_PICK, data=data
            )
            return [] if quiet else [_pick_reply(data["places"])]
        return await _ask(session, dialog, data, states.NATAL_CITY, quiet=quiet)

    if data.get("purpose") == PURPOSE_VIP:
        # Импорт внутри: vip.py импортирует natal ради сбора — без цикла модулей.
        from app.chatbot.flows import vip

        return await vip.after_birth_data(session, user, dialog, data)
    if data.get("purpose") == PURPOSE_PAIR:
        # Импорт внутри по той же причине: pair.py импортирует natal.
        from app.chatbot.flows import pair

        return await pair.after_self_data(session, user, dialog, data)
    return await _launch(session, dialog, data)


async def _ask(
    session: AsyncSession,
    dialog: BotDialog,
    data: dict[str, Any],
    state: str,
    *,
    quiet: bool = False,
) -> list[Outgoing]:
    await storage.save(session, dialog, state=state, data=data)
    if quiet:
        # Уже ответили «не поняла …» с подсказкой — второй вопрос подряд лишний.
        return []
    if state == states.NATAL_DATE:
        return [Reply(text=texts.NATAL_ASK_DATE)]
    if state == states.NATAL_TIME:
        return [
            Reply(
                text=texts.NATAL_ASK_TIME.format(when=human_date(data["date"])),
                buttons=[[Button(texts.BTN_TIME_UNKNOWN, {"cmd": "natal_time_unknown"})]],
                inline=True,
            )
        ]
    return [Reply(text=texts.NATAL_ASK_CITY)]


def _not_understood(state: str) -> str:
    if state == states.NATAL_TIME:
        return texts.NATAL_TIME_NOT_UNDERSTOOD
    if state in {states.NATAL_CITY, states.NATAL_CITY_PICK}:
        return texts.NATAL_CITY_NOT_FOUND
    return texts.NATAL_DATE_NOT_UNDERSTOOD


def _pick_reply(places: list[dict[str, Any]]) -> Reply:
    rows: list[list[Button]] = [
        [Button(p["label"], {"cmd": "natal_place", "i": i})]
        for i, p in enumerate(places)
    ]
    rows.append([Button(texts.BTN_OTHER_CITY, {"cmd": "natal_city_again"})])
    return Reply(text=texts.NATAL_PICK_CITY, buttons=rows, inline=True)


def _place_dict(place: charts_service.Place) -> dict[str, Any]:
    return {"label": place.label, "lat": place.lat, "lon": place.lon, "tz": place.tz}


def _complete(data: dict[str, Any]) -> bool:
    return "date" in data and "time_known" in data and "place" in data


async def _launch(
    session: AsyncSession, dialog: BotDialog, data: dict[str, Any]
) -> list[Outgoing]:
    await storage.save(session, dialog, state=states.NATAL_WAIT, data=data)
    return [Reply(text=texts.NATAL_COUNTING.format(summary=summary(data))), Job(JOB_REPORT)]


def birth_values(data: dict[str, Any]) -> tuple[datetime, bool, charts_service.Place]:
    """Черновик из data → аргументы create_chart / vip.create_order."""
    time_known = bool(data.get("time_known"))
    clock = time.fromisoformat(data["time"]) if time_known else charts_service.UNKNOWN_TIME
    birth_local = datetime.combine(date.fromisoformat(data["date"]), clock)
    p = data["place"]
    place = charts_service.Place(label=p["label"], lat=p["lat"], lon=p["lon"], tz=p["tz"])
    return birth_local, time_known, place


def human_date(iso: str) -> str:
    value = date.fromisoformat(iso)
    return f"{value.day} {_MONTHS_GEN[value.month - 1]} {value.year}"


def summary(data: dict[str, Any]) -> str:
    """«9 марта 1987, время неизвестно, Егорьевск, RU» — для сверки человеком."""
    when = data.get("time") if data.get("time_known") else texts.TIME_UNKNOWN_LABEL
    return f"{human_date(data['date'])}, {when}, {data['place']['label']}"


def _wait_is_stale(dialog: BotDialog) -> bool:
    updated = getattr(dialog, "updated_at", None)
    return updated is not None and datetime.now(UTC) - updated > _WAIT_STALE


async def run_report(
    session: AsyncSession, user: User, dialog: BotDialog
) -> list[Outgoing]:
    """Job: карта → free_natal → текст с кнопками. Коммитит сам: внутри 30–60 с LLM.

    Ошибки LLM превращает в ответ с «Повторить»; черновик в data остаётся.
    """
    data = dict(dialog.data or {})
    if dialog.state != states.NATAL_WAIT or not _complete(data):
        return []

    birth_local, time_known, place = birth_values(data)
    chart = await charts_service.create_chart(
        session,
        user_id=user.id,
        birth_local=birth_local,
        place=place,
        time_known=time_known,
    )
    await events.log(
        session, events.CHART_CREATED,
        user_id=user.id, chart_id=chart.id, time_known=time_known,
    )
    await events.log(
        session, events.REPORT_REQUESTED,
        user_id=user.id, chart_id=chart.id, type=ReportType.FREE_NATAL.value,
    )
    # Карту и события фиксируем до LLM: падение генерации их не откатит.
    await session.commit()
    # rollback() ниже протухает все объекты сессии, а ленивое чтение атрибута
    # в async падает (MissingGreenlet) — id берём заранее.
    user_id, chart_id = user.id, chart.id

    retry = [[Button(texts.BTN_RETRY, {"cmd": "natal_retry"})]]
    try:
        report = await reports.get_or_create_report(session, chart=chart, user=user)
    except reports.ReportError as error:
        await session.rollback()
        logger.info(
            "chatbot.natal denied user_id=%s chart_id=%s reason=%s",
            user_id, chart_id, type(error).__name__,
        )
        await events.log(
            session, events.REPORT_DENIED,
            user_id=user_id, chart_id=chart_id, type=ReportType.FREE_NATAL.value,
            reason=type(error).__name__,
        )
        await storage.save(session, dialog, state=states.IDLE, data=data)
        await session.commit()
        return [Reply(text=texts.NATAL_LIMIT)]
    except Exception as error:  # noqa: BLE001 — LLMError и прочая авария → «Повторить»
        await session.rollback()
        logger.error(
            "chatbot.natal failed user_id=%s chart_id=%s error_type=%s",
            user_id, chart_id, type(error).__name__,
        )
        await events.log(
            session, events.REPORT_FAILED,
            user_id=user_id, chart_id=chart_id, type=ReportType.FREE_NATAL.value,
        )
        await storage.save(session, dialog, state=states.IDLE, data=data)
        await session.commit()
        return [Reply(text=texts.NATAL_FAILED, buttons=retry, inline=True)]

    await events.log(
        session, events.REPORT_READY,
        user_id=user.id, chart_id=chart.id, type=ReportType.FREE_NATAL.value,
    )
    # Черновик храним под last: VIP возьмёт данные отсюда, не спрашивая заново.
    await storage.save(
        session, dialog, state=states.IDLE, data={"last": birth_only(data)}
    )
    await session.commit()

    buttons: list[list[Button]] = []
    if orders.is_for_sale(ReportType.VIP_NATAL):
        buttons.append([Button(texts.BTN_VIP, {"cmd": "vip"}, color="positive")])
    if not user.subscribed:
        buttons.append([Button(texts.BTN_DAILY, {"cmd": "subscribe"})])
    header = texts.NATAL_HEADER.format(summary=summary(data))
    body = _MARKDOWN.sub("", report.text)
    return [Reply(text=f"{header}\n\n{body}", buttons=buttons, inline=True)]


def birth_only(data: dict[str, Any]) -> dict[str, Any]:
    return {k: data[k] for k in ("date", "time", "time_known", "place") if k in data}

