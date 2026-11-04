"""КАРТА и VIP в боте: свободный ввод данных, выбор города, разбор, заказ.

Без БД и сети: диалог в памяти, сервисы карты/разбора/заказа подменены.
Справочник городов настоящий — он в памяти процесса и ради падежей важен.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.chatbot import parse, router, states, texts
from app.chatbot.flows import natal, vip
from app.chatbot.types import Button, Incoming, Job, LinkButton, Reply
from app.models import ClientGender, User

# --- разбор свободного текста ------------------------------------------------

@pytest.mark.parametrize(
    ("text", "expect", "when", "clock", "unknown", "city"),
    [
        ("9,03,1987, время не помню, город Егорьевск", None, date(1987, 3, 9), None, True, "егорьевск"),
        ("родилась 9 марта 87 в 14.30 в Москве", None, date(1987, 3, 9), time(14, 30), False, "москве"),
        ("09.03.1987 14:30 Санкт-Петербург", None, date(1987, 3, 9), time(14, 30), False, "санкт-петербург"),
        ("9 мая 1990г. в 7 утра, Самара", None, date(1990, 5, 9), time(7, 0), False, "самара"),
        ("1987-03-09", None, date(1987, 3, 9), None, False, None),
        ("09031987", None, date(1987, 3, 9), None, False, None),
        ("в 2 часа ночи", "time", None, time(2, 0), False, None),
        ("в 8 вечера", "time", None, time(20, 0), False, None),
        ("14", "time", None, time(14, 0), False, None),
        ("не знаю", "time", None, None, True, None),
        ("г. Казань", None, None, None, False, "казань"),
        ("из Ростова-на-Дону", "city", None, None, False, "ростова-на-дону"),
    ],
)
def test_parse_birth_free_form(text, expect, when, clock, unknown, city) -> None:
    guess = parse.parse_birth(text, expect=expect)
    assert guess.birth_date == when
    assert guess.birth_time == clock
    assert guess.time_unknown is unknown
    assert guess.city == city


def test_parse_ignores_chat_and_flags_impossible_date() -> None:
    assert parse.parse_birth("привет, хочу разбор", expect="date").city is None
    assert parse.parse_birth("14").birth_time is None  # без вопроса о времени это не час
    assert parse.parse_birth("31.02.1990").bad_date is True
    assert parse.parse_birth("01.01.1850").bad_date is True


@pytest.mark.parametrize(
    ("query", "label"),
    [
        ("егорьевск", "Егорьевск, RU"),
        ("москве", "Москва, RU"),
        ("нижнем новгороде", "Нижний Новгород, RU"),
        ("ростова-на-дону", "Ростов-на-Дону, RU"),
        ("егорьевск московской области", "Егорьевск, RU"),
    ],
)
def test_find_places_handles_cases(query: str, label: str) -> None:
    found = parse.find_places(query)
    assert found.sure is True
    assert found.places[0].label == label


def test_find_places_unknown_city() -> None:
    assert parse.find_places("абырвалгск").places == []


# --- сценарий через роутер ---------------------------------------------------

@dataclass
class FakeUser:
    id: int = 1
    external_id: str = "100"
    subscribed: bool = False
    sun_sign: str | None = None
    primary_chart_id: int | None = None


@dataclass
class FakeDialog:
    user_id: int = 1
    state: str = states.IDLE
    data: dict[str, Any] = field(default_factory=dict)
    updated_at: datetime | None = None


@dataclass
class FakeSession:
    added: list[Any] = field(default_factory=list)
    commit: AsyncMock = field(default_factory=AsyncMock)
    rollback: AsyncMock = field(default_factory=AsyncMock)

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def event_names(self) -> list[str]:
        return [e.name for e in self.added]


@pytest.fixture
def dialog() -> FakeDialog:
    return FakeDialog()


@pytest.fixture
def user() -> FakeUser:
    return FakeUser()


@pytest.fixture
def session() -> FakeSession:
    return FakeSession()


@pytest.fixture(autouse=True)
def patched_storage(monkeypatch: pytest.MonkeyPatch, dialog: FakeDialog) -> None:
    async def fake_load(_s: Any, _uid: int) -> FakeDialog:
        return dialog

    async def fake_save(_s: Any, d: FakeDialog, *, state=None, data=None) -> None:
        if state is not None:
            d.state = state
        if data is not None:
            d.data = data

    async def fake_reset(_s: Any, d: FakeDialog) -> None:
        d.state, d.data = states.IDLE, {}

    # storage — один модуль на всех: патч виден router, natal и vip.
    monkeypatch.setattr(router.storage, "load", fake_load)
    monkeypatch.setattr(router.storage, "save", fake_save)
    monkeypatch.setattr(router.storage, "reset", fake_reset)


async def _say(session, user, text: str = "", payload: dict | None = None):
    incoming = Incoming(platform="vk", external_id="100", text=text, payload=payload)
    return await router.handle(cast(AsyncSession, session), cast(User, user), incoming)


def _texts(outgoing) -> list[str]:
    return [o.text for o in outgoing if isinstance(o, Reply)]


async def test_all_in_one_message_without_code_word(session, user, dialog) -> None:
    """Реальный случай: данные сразу, без КАРТА, время «не помню»."""
    out = await _say(session, user, "9,03,1987, время не помню, город Егорьевск")
    assert dialog.state == states.NATAL_WAIT
    assert isinstance(out[-1], Job) and out[-1].name == natal.JOB_REPORT
    assert "9 марта 1987, время неизвестно, Егорьевск, RU" in out[0].text
    assert dialog.data["time_known"] is False
    assert "bot_natal_started" in session.event_names()


async def test_step_by_step_asks_only_missing(session, user, dialog) -> None:
    await _say(session, user, "КАРТА")
    out = await _say(session, user, "9 марта 1987")
    assert dialog.state == states.NATAL_TIME
    assert out[0].text.startswith("9 марта 1987 — записала")
    out = await _say(session, user, "", {"cmd": "natal_time_unknown"})
    assert dialog.state == states.NATAL_CITY
    assert _texts(out) == [texts.NATAL_ASK_CITY]
    out = await _say(session, user, "в Егорьевске")
    assert dialog.state == states.NATAL_WAIT
    assert isinstance(out[-1], Job)


async def test_code_word_with_data(session, user, dialog) -> None:
    out = await _say(session, user, "карта 11.05.1990 08:15 Казань")
    assert dialog.state == states.NATAL_WAIT
    assert dialog.data["time"] == "08:15"
    assert isinstance(out[-1], Job)


async def test_unknown_city_keeps_date(session, user, dialog) -> None:
    out = await _say(session, user, "9.03.1987 14:00 город Абырвалгск")
    assert dialog.state == states.NATAL_CITY
    assert dialog.data["date"] == "1987-03-09"
    assert _texts(out) == [texts.NATAL_CITY_NOT_FOUND]


async def test_ambiguous_city_shows_buttons_then_pick(session, user, dialog) -> None:
    dialog.state = states.NATAL_CITY
    dialog.data = {"purpose": "free", "date": "1987-03-09", "time_known": False}
    out = await _say(session, user, "Нижн")
    assert dialog.state == states.NATAL_CITY_PICK
    rows = out[0].buttons
    assert rows[-1][0].payload == {"cmd": "natal_city_again"}
    out = await _say(session, user, "", {"cmd": "natal_place", "i": 0})
    assert dialog.state == states.NATAL_WAIT
    assert isinstance(out[-1], Job)


async def test_gibberish_reasks_with_hint(session, user, dialog) -> None:
    await _say(session, user, "КАРТА")
    out = await _say(session, user, "ну не знаю даже")
    # «не знаю» — это время; дату всё ещё ждём и не задаём второй вопрос подряд.
    assert dialog.state == states.NATAL_DATE
    assert dialog.data["time_known"] is False
    assert len(out) == 1


async def test_wait_state_answers_already_counting(session, user, dialog) -> None:
    dialog.state = states.NATAL_WAIT
    out = await _say(session, user, "КАРТА")
    assert _texts(out) == [texts.NATAL_ALREADY_WAITING]


async def test_plain_chat_in_idle_still_menu(session, user, dialog) -> None:
    out = await _say(session, user, "привет")
    assert out[0].text == texts.MENU_TEXT
    assert dialog.state == states.IDLE


# --- Job: разбор -------------------------------------------------------------

def _ready_dialog(dialog: FakeDialog) -> None:
    dialog.state = states.NATAL_WAIT
    dialog.data = {
        "purpose": "free",
        "date": "1987-03-09",
        "time_known": False,
        "place": {"label": "Егорьевск, RU", "lat": 55.38, "lon": 39.03, "tz": "Europe/Moscow"},
    }


async def test_run_report_success(monkeypatch, session, user, dialog) -> None:
    _ready_dialog(dialog)
    create = AsyncMock(return_value=SimpleNamespace(id=55))
    monkeypatch.setattr(natal.charts_service, "create_chart", create)
    monkeypatch.setattr(
        natal.reports, "get_or_create_report",
        AsyncMock(return_value=SimpleNamespace(text="## Солнце\nТекст разбора")),
    )
    out = await router.run_job(session, user, Job(natal.JOB_REPORT))
    kwargs = create.await_args.kwargs
    assert kwargs["time_known"] is False
    assert kwargs["birth_local"].date() == date(1987, 3, 9)
    assert dialog.state == states.IDLE
    assert dialog.data["last"]["place"]["label"] == "Егорьевск, RU"
    assert out[0].text.startswith(texts.NATAL_HEADER.split("{")[0])
    payloads = [b.payload["cmd"] for row in out[0].buttons for b in row if isinstance(b, Button)]
    assert payloads == ["vip", "subscribe"]
    assert {"chart_created", "report_requested", "report_ready"} <= set(session.event_names())
    assert user.subscribed is False  # КАРТА не подписывает


async def test_run_report_llm_failure_offers_retry(monkeypatch, session, user, dialog) -> None:
    _ready_dialog(dialog)
    monkeypatch.setattr(
        natal.charts_service, "create_chart", AsyncMock(return_value=SimpleNamespace(id=55))
    )
    monkeypatch.setattr(
        natal.reports, "get_or_create_report", AsyncMock(side_effect=RuntimeError("timeout"))
    )
    out = await router.run_job(session, user, Job(natal.JOB_REPORT))
    assert out[0].text == texts.NATAL_FAILED
    assert out[0].buttons[0][0].payload == {"cmd": "natal_retry"}
    assert "place" in dialog.data  # повтор не спросит данные заново
    session.rollback.assert_awaited()


# --- VIP ---------------------------------------------------------------------

async def test_vip_reuses_last_chart_and_creates_order(monkeypatch, session, user, dialog) -> None:
    _ready_dialog(dialog)
    dialog.state = states.IDLE
    dialog.data = {"last": natal.birth_only(dialog.data)}
    order = SimpleNamespace(id=9, token="tok")
    create = AsyncMock(return_value=order)
    monkeypatch.setattr(vip.vip_service, "create_order", create)
    monkeypatch.setattr(vip.vip_service, "payment_url", lambda o: "https://x/pay/order/tok")

    out = await _say(session, user, "", {"cmd": "vip"})
    assert "149" in out[0].text or "₽" in out[0].text
    out = await _say(session, user, "", {"cmd": "vip_go"})
    assert dialog.state == states.VIP_NAME
    assert "Егорьевск" in out[0].text
    await _say(session, user, "Меня зовут Анна")
    assert dialog.data["name"] == "Анна"
    await _say(session, user, "", {"cmd": "vip_gender", "g": "female"})
    assert dialog.state == states.VIP_CONFIRM
    out = await _say(session, user, "", {"cmd": "vip_confirm"})

    kwargs = create.await_args.kwargs
    assert kwargs["vk_user_id"] == 100
    assert kwargs["client_gender"] is ClientGender.FEMALE
    assert kwargs["person_name"] == "Анна"
    assert isinstance(out[0].buttons[0][0], LinkButton)
    assert out[0].buttons[0][0].url.endswith("/pay/order/tok")
    assert dialog.state == states.IDLE


async def test_vip_without_chart_collects_data_first(session, user, dialog) -> None:
    await _say(session, user, "", {"cmd": "vip_go"})
    assert dialog.state == states.NATAL_DATE
    out = await _say(session, user, "9,03,1987, время не помню, город Егорьевск")
    # purpose=vip: после данных не разбор, а вопрос об имени.
    assert dialog.state == states.VIP_NAME
    assert not any(isinstance(o, Job) for o in out)


async def test_vip_payment_unavailable(monkeypatch, session, user, dialog) -> None:
    _ready_dialog(dialog)
    dialog.state = states.VIP_CONFIRM
    dialog.data |= {"name": None, "gender": "male"}
    monkeypatch.setattr(
        vip.vip_service, "create_order", AsyncMock(return_value=SimpleNamespace(id=9))
    )
    monkeypatch.setattr(vip.vip_service, "payment_url", lambda o: None)
    out = await _say(session, user, "", {"cmd": "vip_confirm"})
    assert out[0].text == texts.VIP_PAY_UNAVAILABLE
