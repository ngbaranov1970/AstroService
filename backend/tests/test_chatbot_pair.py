"""ПАРА в боте: свои данные, партнёр, тизер, заказ. Без БД, сети и LLM.

Диалог в памяти; тизер, заказ и карта подменены. Справочник городов настоящий.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.chatbot import router, states, texts
from app.chatbot.flows import pair
from app.chatbot.types import Button, Incoming, Job, LinkButton, Reply
from app.models import OrderStatus, User
from app.services import pair_teaser

LAST = {
    "date": "1987-03-09", "time": "14:30", "time_known": True,
    "place": {"label": "Егорьевск, RU", "lat": 55.3833, "lon": 39.0333, "tz": "Europe/Moscow"},
}


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

    def events(self) -> list[tuple[str, dict]]:
        return [(e.name, e.props) for e in self.added]

    def event_names(self) -> list[str]:
        return [e.name for e in self.added]


@pytest.fixture
def dialog() -> FakeDialog:
    return FakeDialog(data={"last": dict(LAST)})


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

    monkeypatch.setattr(router.storage, "load", fake_load)
    monkeypatch.setattr(router.storage, "save", fake_save)
    monkeypatch.setattr(router.storage, "reset", fake_reset)


@pytest.fixture
def services(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Подмены карты, тизера и заказа; запоминают, с чем их звали."""
    chart = SimpleNamespace(id=7, calc={"natal": {}}, time_known=True)
    teaser = pair_teaser.Teaser(strong="вас тянет", spark="он тормозит", more="темы", image=b"png")
    order = SimpleNamespace(id=31, status=OrderStatus.PENDING, amount=39_000, token="tok")
    ns = SimpleNamespace(
        create_chart=AsyncMock(return_value=chart),
        partner_data=AsyncMock(return_value={"natal": {}}),
        make=AsyncMock(return_value=teaser),
        create_order=AsyncMock(return_value=order),
        order=order,
    )
    monkeypatch.setattr(pair.charts_service, "create_chart", ns.create_chart)
    monkeypatch.setattr(pair.vip_pair, "partner_data", ns.partner_data)
    monkeypatch.setattr(pair.pair_teaser, "make", ns.make)
    monkeypatch.setattr(pair.vip_pair, "create_order", ns.create_order)
    monkeypatch.setattr(
        pair.vip_service, "payment_url", lambda o: f"https://astrozvezdochet.ru/pay/order/{o.token}"
    )
    return ns


async def _say(session, user, text: str = "", payload: dict | None = None, *, check=None, ref=None):
    incoming = Incoming(platform="vk", external_id="100", text=text, payload=payload, ref=ref)
    return await router.handle(
        cast(AsyncSession, session), cast(User, user), incoming, is_member=check
    )


async def _job(session, user, out):
    assert isinstance(out[-1], Job) and out[-1].name == pair.JOB_TEASER
    return await router.run_job(cast(AsyncSession, session), cast(User, user), out[-1])


def _texts(outgoing) -> list[str]:
    return [o.text for o in outgoing if isinstance(o, Reply)]


def _cmds(reply: Reply) -> list[str]:
    return [b.payload["cmd"] for row in reply.buttons for b in row if isinstance(b, Button)]


async def _to_partner(session, user) -> None:
    await _say(session, user, "ПАРА")
    await _say(session, user, "", {"cmd": "pair_self_ok"})


async def _to_teaser(session, user):
    """ПАРА → «Да» → партнёр только датой → без времени → без имени → Job."""
    await _to_partner(session, user)
    await _say(session, user, "11.05.1990")
    await _say(session, user, "", {"cmd": "pair_time_unknown"})
    return await _say(session, user, "", {"cmd": "pair_skip_name"})


# --- свои данные ---------------------------------------------------------------


async def test_confirms_last_data_with_yes(session, user, dialog) -> None:
    out = await _say(session, user, "ПАРА")
    assert dialog.state == states.PAIR_SELF
    assert "9 марта 1987, 14:30, Егорьевск, RU" in out[0].text
    assert _cmds(out[0]) == ["pair_self_ok", "pair_self_other"]

    out = await _say(session, user, "", {"cmd": "pair_self_ok"})
    assert dialog.state == states.PAIR_PARTNER
    assert _texts(out) == [texts.PAIR_ASK_PARTNER]
    assert dialog.data["pair"]["self"] == LAST
    assert dialog.data["last"] == LAST


async def test_other_data_collects_self_then_asks_partner(session, user, dialog) -> None:
    await _say(session, user, "ПАРА")
    out = await _say(session, user, "", {"cmd": "pair_self_other"})
    assert dialog.state == states.NATAL_DATE
    assert _texts(out) == [texts.PAIR_ASK_SELF]

    out = await _say(session, user, "1.02.1991, 08:00, Самара")
    assert dialog.state == states.PAIR_PARTNER
    assert _texts(out) == [texts.PAIR_ASK_PARTNER]
    assert dialog.data["pair"]["self"]["date"] == "1991-02-01"
    # Черновик сбора на верхнем уровне убран, last — уже новые данные.
    assert "date" not in dialog.data and dialog.data["last"]["date"] == "1991-02-01"
    # Бесплатную КАРТУ сбор для пары не запускает и её событий не пишет.
    assert "bot_natal_started" not in session.event_names()


async def test_without_last_starts_with_own_data(session, user, dialog) -> None:
    dialog.data = {}
    out = await _say(session, user, "ПАРА")
    assert dialog.state == states.NATAL_DATE
    assert _texts(out) == [texts.PAIR_ASK_SELF]


# --- партнёр -------------------------------------------------------------------


async def test_partner_without_time_is_not_asked_for_city(session, user, dialog, services) -> None:
    await _to_partner(session, user)
    out = await _say(session, user, "11 мая 1990")
    assert out[0].text.startswith("11 мая 1990 — записала")
    out = await _say(session, user, "", {"cmd": "pair_time_unknown"})
    # Сразу имя: город без времени не нужен.
    assert _texts(out) == [texts.PAIR_ASK_NAME]
    out = await _say(session, user, "Маша")
    assert dialog.state == states.PAIR_TEASER
    assert _texts(out) == [texts.PAIR_COUNTING]
    assert dialog.data["pair"]["partner"] == {"date": "1990-05-11", "time_known": False, "name": "Маша"}


async def test_partner_with_time_is_asked_for_city(session, user, dialog, services) -> None:
    await _to_partner(session, user)
    await _say(session, user, "11.05.1990")
    out = await _say(session, user, "7:15")
    assert _texts(out) == [texts.PAIR_ASK_PARTNER_CITY]
    out = await _say(session, user, "Самара")
    assert _texts(out) == [texts.PAIR_ASK_NAME]
    partner = dialog.data["pair"]["partner"]
    assert partner["time"] == "07:15" and partner["place"]["label"] == "Самара, RU"


async def test_partner_all_in_one_message(session, user, dialog, services) -> None:
    await _to_partner(session, user)
    out = await _say(session, user, "11.05.1990, 7:15, Самара")
    assert _texts(out) == [texts.PAIR_ASK_NAME]


async def test_word_instead_of_date_is_not_a_city(session, user, dialog) -> None:
    await _to_partner(session, user)
    out = await _say(session, user, "не помню точно")
    assert _texts(out) == [texts.PAIR_DATE_NOT_UNDERSTOOD]
    assert dialog.state == states.PAIR_PARTNER


async def test_para_again_in_the_middle_restarts(session, user, dialog) -> None:
    await _to_partner(session, user)
    await _say(session, user, "11.05.1990")
    out = await _say(session, user, "ПАРА")
    assert dialog.state == states.PAIR_SELF
    assert "partner" not in dialog.data["pair"]
    assert _cmds(out[0]) == ["pair_self_ok", "pair_self_other"]
    assert dialog.data["last"] == LAST


# --- гейт ----------------------------------------------------------------------


async def test_gate_stops_before_first_question(session, user, dialog) -> None:
    async def not_member() -> bool:
        return False

    out = await _say(session, user, "ПАРА", check=not_member)
    assert _texts(out) == [texts.GATE_PAIR]
    assert dialog.state == states.IDLE and "pair" not in dialog.data
    assert session.event_names() == ["gate_shown"]


async def test_direct_entry_skips_gate_and_teaser(session, user, dialog, services) -> None:
    async def not_member() -> bool:
        raise AssertionError("у входа с экрана VIP гейта нет")

    out = await _say(session, user, "", {"cmd": "pair_vip"}, check=not_member)
    assert dialog.state == states.PAIR_SELF
    await _say(session, user, "", {"cmd": "pair_self_ok"}, check=not_member)
    await _say(session, user, "11.05.1990", check=not_member)
    await _say(session, user, "", {"cmd": "pair_time_unknown"}, check=not_member)
    out = await _say(session, user, "", {"cmd": "pair_skip_name"}, check=not_member)

    # Сразу описание с ценой: ни Job, ни вызова модели.
    assert not any(isinstance(o, Job) for o in out)
    assert _texts(out) == [texts.PAIR_INTRO.format(price=390)]
    services.make.assert_not_awaited()
    assert session.event_names() == ["pair_started", "pair_partner_given"]
    assert {props["ref"] for _, props in session.events()} == {"pair_vip"}


# --- тизер ---------------------------------------------------------------------


async def test_teaser_reply_has_three_lines_image_and_button(session, user, dialog, services) -> None:
    out = await _to_teaser(session, user)
    [reply] = await _job(session, user, out)
    assert reply.image == b"png"
    assert reply.text.startswith(texts.PAIR_TEASER_HEADER)
    for label in ("Сильное: вас тянет", "Где искрит: он тормозит", "Что видно только в полном разборе: темы"):
        assert label in reply.text
    assert _cmds(reply) == ["pair_full"]
    assert dialog.state == states.IDLE and dialog.data["pair"]["partner"]["date"] == "1990-05-11"
    # Партнёр без времени считается в городе клиента: место партнёра не передаём.
    partner = services.partner_data.await_args.args[0]
    assert partner.birth_date == date(1990, 5, 11) and partner.birth_time is None and partner.place is None


async def test_budget_refusal_keeps_data(session, user, dialog, services) -> None:
    services.make.side_effect = pair_teaser.TeaserBudgetError("потолок")
    out = await _to_teaser(session, user)
    [reply] = await _job(session, user, out)
    assert reply.text == texts.PAIR_LIMIT and not reply.buttons
    assert dialog.state == states.IDLE and "pair" in dialog.data
    assert "pair_teaser_shown" not in session.event_names()


async def test_failure_offers_retry_and_retry_works(session, user, dialog, services) -> None:
    services.make.side_effect = pair_teaser.TeaserError("модель не ответила")
    out = await _to_teaser(session, user)
    [reply] = await _job(session, user, out)
    assert reply.text == texts.PAIR_FAILED and _cmds(reply) == ["pair_retry"]

    services.make.side_effect = None
    out = await _say(session, user, "", {"cmd": "pair_retry"})
    assert dialog.state == states.PAIR_TEASER
    [reply] = await _job(session, user, out)
    assert reply.image == b"png"
    assert session.event_names().count("pair_teaser_shown") == 1


# --- заказ ---------------------------------------------------------------------


async def _to_order(session, user):
    out = await _to_teaser(session, user)
    await _job(session, user, out)
    await _say(session, user, "", {"cmd": "pair_full"})
    return await _say(session, user, "", {"cmd": "pair_go"})


async def test_summary_warns_about_partner_without_time(session, user, dialog, services) -> None:
    [reply] = await _to_order(session, user)
    assert dialog.state == states.PAIR_ORDER
    assert "Ты: 9 марта 1987, 14:30, Егорьевск, RU" in reply.text
    assert "Партнёр: 11 мая 1990, время неизвестно" in reply.text
    assert texts.PAIR_NO_TIME_NOTE in reply.text
    assert _cmds(reply) == ["pair_confirm", "pair_fix"]


async def test_confirm_creates_order_and_gives_pay_link(session, user, dialog, services) -> None:
    await _to_order(session, user)
    [reply] = await _say(session, user, "", {"cmd": "pair_confirm"})
    assert reply.text == texts.PAIR_PAY
    button = reply.buttons[0][0]
    assert isinstance(button, LinkButton)
    assert button.url == "https://astrozvezdochet.ru/pay/order/tok"
    assert button.label == "Оплатить 390 ₽"
    kwargs = services.create_order.await_args.kwargs
    assert kwargs["ref"] == "pair_dm" and kwargs["partner"].birth_date == date(1990, 5, 11)
    # Партнёр ушёл в заказ, в диалоге остались только свои данные.
    assert dialog.state == states.IDLE and dialog.data == {"last": LAST}


async def test_fix_goes_back_to_own_data_without_second_started(session, user, dialog, services) -> None:
    await _to_order(session, user)
    out = await _say(session, user, "", {"cmd": "pair_fix"})
    assert dialog.state == states.PAIR_SELF
    assert _cmds(out[0]) == ["pair_self_ok", "pair_self_other"]
    assert session.event_names().count("pair_started") == 1


async def test_already_paid_pair_gets_no_link(session, user, dialog, services) -> None:
    services.order.status = OrderStatus.PAID
    await _to_order(session, user)
    [reply] = await _say(session, user, "", {"cmd": "pair_confirm"})
    assert reply.text == texts.PAIR_ALREADY_PAID and not reply.buttons


async def test_ref_is_in_every_event(session, user, dialog, services) -> None:
    await _say(session, user, "ПАРА", ref="clip_7")
    await _say(session, user, "", {"cmd": "pair_self_ok"})
    await _say(session, user, "11.05.1990")
    await _say(session, user, "", {"cmd": "pair_time_unknown"})
    out = await _say(session, user, "", {"cmd": "pair_skip_name"})
    await _job(session, user, out)
    await _say(session, user, "", {"cmd": "pair_full"})
    await _say(session, user, "", {"cmd": "pair_go"})
    await _say(session, user, "", {"cmd": "pair_confirm"})
    assert session.event_names() == [
        "pair_started", "pair_partner_given", "pair_teaser_shown", "pair_order_created",
    ]
    assert [props["ref"] for _, props in session.events()] == ["clip_7"] * 4
    assert session.events()[-1][1]["order_id"] == 31


async def test_menu_button_and_comment_have_own_ref(session, user, dialog) -> None:
    await _say(session, user, "", {"cmd": "pair"})
    assert dialog.data["pair"]["ref"] == "pair_menu"
    await _say(session, user, "ПАРА", ref="pair_post")
    assert dialog.data["pair"]["ref"] == "pair_post"


async def test_old_button_after_reset_does_not_crash(session, user, dialog) -> None:
    dialog.data = {}
    out = await _say(session, user, "", {"cmd": "pair_confirm"})
    assert _texts(out) == [texts.PAIR_EXPIRED]
