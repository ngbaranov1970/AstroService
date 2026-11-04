"""Меню и выбор знака: дата → знак, куспид → две кнопки, повтор меняет знак."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.chatbot import router, states, texts
from app.chatbot.flows import menu
from app.chatbot.types import Incoming, Reply
from app.models import User
from app.services import events


@dataclass
class FakeUser:
    id: int = 1
    subscribed: bool = False
    sun_sign: str | None = None
    primary_chart_id: int | None = None


@dataclass
class FakeDialog:
    user_id: int = 1
    state: str = states.IDLE
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)


@pytest.fixture
def dialog() -> FakeDialog:
    return FakeDialog()


@pytest.fixture
def user() -> FakeUser:
    return FakeUser()


@pytest.fixture
def session() -> FakeSession:
    return FakeSession()


@pytest.fixture
def patched_storage(
    monkeypatch: pytest.MonkeyPatch, dialog: FakeDialog
) -> FakeDialog:
    async def fake_load(_session: Any, user_id: int) -> FakeDialog:
        dialog.user_id = user_id
        return dialog

    async def fake_save(
        _session: Any,
        d: FakeDialog,
        *,
        state: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        if state is not None:
            d.state = state
        if data is not None:
            d.data = data

    async def fake_reset(_session: Any, d: FakeDialog) -> None:
        d.state = states.IDLE
        d.data = {}

    monkeypatch.setattr(router.storage, "load", fake_load)
    monkeypatch.setattr(router.storage, "save", fake_save)
    monkeypatch.setattr(router.storage, "reset", fake_reset)
    monkeypatch.setattr(menu.storage, "load", fake_load)
    monkeypatch.setattr(menu.storage, "save", fake_save)
    monkeypatch.setattr(menu.storage, "reset", fake_reset)
    return dialog


@pytest.fixture
def logged(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []

    async def fake_log(_session: Any, name: str, **props: Any) -> None:
        calls.append({"name": name, **props})

    def fake_set_sun_sign(u: FakeUser, sign: str) -> bool:
        changed = u.sun_sign != sign
        u.sun_sign = sign
        return changed

    monkeypatch.setattr(menu.sun_sign_service.events, "log", fake_log)
    monkeypatch.setattr(menu.sun_sign_service.users_service, "set_sun_sign", fake_set_sun_sign)
    return calls


def _incoming(text: str, *, payload: dict[str, Any] | None = None) -> Incoming:
    return Incoming(platform="vk", external_id="100", text=text, payload=payload)


async def _handle(
    session: FakeSession, user: FakeUser, incoming: Incoming
) -> list[Reply]:
    return await router.handle(
        cast(AsyncSession, session),
        cast(User, user),
        incoming,
    )


def test_parse_birth_date_formats() -> None:
    from datetime import date

    assert menu.parse_birth_date("11.05.1990") == date(1990, 5, 11)
    assert menu.parse_birth_date("11/5/1990") == date(1990, 5, 11)
    assert menu.parse_birth_date("не дата") is None


async def test_my_sign_opens_keyboard(
    session: FakeSession,
    user: FakeUser,
    dialog: FakeDialog,
    patched_storage: FakeDialog,
) -> None:
    assert patched_storage is dialog
    replies = await _handle(
        session, user, _incoming("", payload={"cmd": "sign"})
    )
    assert dialog.state == states.ASK_SIGN
    assert len(replies) == 2
    assert replies[0].text == texts.ASK_SIGN
    assert replies[0].inline is True
    # Inline VK ≤10 кнопок: 6 знаков, затем 6 + дата; по два в ряд — иначе
    # «♊ Близнецы» не влезает на телефоне.
    assert sum(len(row) for row in replies[0].buttons) == 6
    assert all(len(row) == 2 for row in replies[0].buttons)
    assert replies[0].buttons[0][0].label == "♈ Овен"
    assert replies[1].text == texts.ASK_SIGN_MORE
    assert replies[1].buttons[-1][0].label == texts.BTN_ENTER_DATE
    assert sum(len(row) for row in replies[1].buttons) == 7


async def test_date_1990_05_11_sets_taurus(
    session: FakeSession,
    user: FakeUser,
    dialog: FakeDialog,
    patched_storage: FakeDialog,
    logged: list[dict],
) -> None:
    assert patched_storage is dialog
    dialog.state = states.ASK_SIGN
    replies = await _handle(session, user, _incoming("11.05.1990"))
    assert user.sun_sign == "Телец"
    assert dialog.state == states.IDLE
    assert replies[0].text == texts.SIGN_SAVED.format(sign="Телец")
    assert replies[-1].text == texts.MENU_TEXT
    assert logged == [
        {"name": events.SUN_SIGN_ENTERED, "user_id": 1, "sign": "Телец"}
    ]


async def test_cusp_1985_06_22_offers_two_signs(
    session: FakeSession,
    user: FakeUser,
    dialog: FakeDialog,
    patched_storage: FakeDialog,
    logged: list[dict],
) -> None:
    assert patched_storage is dialog
    dialog.state = states.ASK_SIGN
    replies = await _handle(session, user, _incoming("22.06.1985"))
    assert user.sun_sign is None
    assert dialog.state == states.ASK_SIGN
    assert replies[0].text == texts.ASK_SIGN_CUSP
    labels = [b.label for b in replies[0].buttons[0]]
    assert labels == ["♊ Близнецы", "♋ Рак"]
    assert logged == []


async def test_cusp_choice_saves_sign(
    session: FakeSession,
    user: FakeUser,
    dialog: FakeDialog,
    patched_storage: FakeDialog,
    logged: list[dict],
) -> None:
    assert patched_storage is dialog
    dialog.state = states.ASK_SIGN
    dialog.data = {"mode": "cusp"}
    replies = await _handle(
        session, user, _incoming("", payload={"cmd": "set_sign", "sign": "Рак"})
    )
    assert user.sun_sign == "Рак"
    assert dialog.state == states.IDLE
    assert replies[0].text == texts.SIGN_SAVED.format(sign="Рак")
    assert logged == [
        {"name": events.SUN_SIGN_ENTERED, "user_id": 1, "sign": "Рак"}
    ]


async def test_my_sign_again_changes_value(
    session: FakeSession,
    user: FakeUser,
    dialog: FakeDialog,
    patched_storage: FakeDialog,
    logged: list[dict],
) -> None:
    assert patched_storage is dialog
    user.sun_sign = "Телец"
    replies = await _handle(
        session, user, _incoming("", payload={"cmd": "sign"})
    )
    assert texts.ASK_SIGN_AGAIN.format(sign="Телец") == replies[0].text

    await _handle(
        session, user, _incoming("", payload={"cmd": "set_sign", "sign": "Лев"})
    )
    assert user.sun_sign == "Лев"
    assert logged == [
        {"name": events.SUN_SIGN_ENTERED, "user_id": 1, "sign": "Лев"}
    ]


async def test_today_without_sign_asks_sign(
    session: FakeSession,
    user: FakeUser,
    dialog: FakeDialog,
    patched_storage: FakeDialog,
) -> None:
    assert patched_storage is dialog
    replies = await _handle(
        session, user, _incoming("", payload={"cmd": "today"})
    )
    assert dialog.state == states.ASK_SIGN
    assert dialog.data.get("pending") == "today"
    assert replies[0].text == texts.ASK_SIGN


async def test_today_with_sign_returns_letter(
    session: FakeSession,
    user: FakeUser,
    dialog: FakeDialog,
    patched_storage: FakeDialog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert patched_storage is dialog
    user.sun_sign = "Лев"
    letter = "Лев\nСегодня хороший день."

    async def fake_letters(_session: Any, _day: Any) -> dict[str, str]:
        return {"Лев": letter}

    monkeypatch.setattr(menu.broadcast, "letters_for", fake_letters)
    replies = await _handle(
        session, user, _incoming("", payload={"cmd": "today"})
    )
    assert dialog.state == states.IDLE
    assert replies[0].text == letter
    assert replies[0].buttons == [
        [menu.Button(texts.BTN_DAILY, {"cmd": "subscribe"})]
    ]
    [event] = session.added
    assert (event.name, event.props) == (menu.events.BOT_TODAY_SHOWN, {"sign": "Лев"})
    assert replies[0].inline is True


async def test_today_subscribed_omits_daily_button(
    session: FakeSession,
    user: FakeUser,
    dialog: FakeDialog,
    patched_storage: FakeDialog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert patched_storage is dialog
    user.sun_sign = "Лев"
    user.subscribed = True

    async def fake_letters(_session: Any, _day: Any) -> dict[str, str]:
        return {"Лев": "текст"}

    monkeypatch.setattr(menu.broadcast, "letters_for", fake_letters)
    replies = await _handle(
        session, user, _incoming("", payload={"cmd": "today"})
    )
    assert replies[0].text == "текст"
    assert replies[0].buttons == []
    assert replies[0].inline is False


async def test_today_without_post_says_not_ready(
    session: FakeSession,
    user: FakeUser,
    dialog: FakeDialog,
    patched_storage: FakeDialog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert patched_storage is dialog
    user.sun_sign = "Лев"

    async def fake_letters(_session: Any, _day: Any) -> dict[str, str]:
        raise LookupError("нет поста")

    monkeypatch.setattr(menu.broadcast, "letters_for", fake_letters)
    replies = await _handle(
        session, user, _incoming("", payload={"cmd": "today"})
    )
    assert replies[0].text == texts.TODAY_NOT_READY
    assert replies[1].text == texts.MENU_TEXT


async def test_month_with_sign_returns_letter(
    session: FakeSession,
    user: FakeUser,
    dialog: FakeDialog,
    patched_storage: FakeDialog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert patched_storage is dialog
    user.sun_sign = "Лев"
    letter = "Вступление\n\nЛев\nНоябрь яркий."

    async def fake_month(_session: Any, _day: Any) -> dict[str, str]:
        return {"Лев": letter}

    monkeypatch.setattr(menu.broadcast, "month_letters_for", fake_month)
    replies = await _handle(
        session, user, _incoming("", payload={"cmd": "month"})
    )
    assert dialog.state == states.IDLE
    assert replies[0].text == letter
    assert replies[0].buttons == [
        [menu.Button(texts.BTN_DAILY, {"cmd": "subscribe"})]
    ]
    [event] = session.added
    assert (event.name, event.props) == (menu.events.BOT_MONTH_SHOWN, {"sign": "Лев"})


async def test_month_without_post_says_not_ready(
    session: FakeSession,
    user: FakeUser,
    dialog: FakeDialog,
    patched_storage: FakeDialog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert patched_storage is dialog
    user.sun_sign = "Лев"

    async def fake_month(_session: Any, _day: Any) -> dict[str, str]:
        raise LookupError("нет поста")

    monkeypatch.setattr(menu.broadcast, "month_letters_for", fake_month)
    replies = await _handle(
        session, user, _incoming("", payload={"cmd": "month"})
    )
    assert replies[0].text == texts.MONTH_NOT_READY
    assert replies[1].text == texts.MENU_TEXT


async def test_month_without_sign_asks_sign(
    session: FakeSession,
    user: FakeUser,
    dialog: FakeDialog,
    patched_storage: FakeDialog,
) -> None:
    assert patched_storage is dialog
    replies = await _handle(
        session, user, _incoming("", payload={"cmd": "month"})
    )
    assert dialog.state == states.ASK_SIGN
    assert dialog.data.get("pending") == "month"
    assert replies[0].text == texts.ASK_SIGN


async def test_pending_month_after_sign_returns_letter(
    session: FakeSession,
    user: FakeUser,
    dialog: FakeDialog,
    patched_storage: FakeDialog,
    logged: list[dict],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert patched_storage is dialog
    dialog.state = states.ASK_SIGN
    dialog.data = {"pending": "month"}
    letter = "Рак\nМесяц спокойный."

    async def fake_month(_session: Any, _day: Any) -> dict[str, str]:
        return {"Рак": letter}

    monkeypatch.setattr(menu.broadcast, "month_letters_for", fake_month)
    replies = await _handle(
        session, user, _incoming("", payload={"cmd": "set_sign", "sign": "Рак"})
    )
    assert user.sun_sign == "Рак"
    assert replies[0].text == texts.SIGN_SAVED.format(sign="Рак")
    assert replies[1].text == letter
    assert logged[0]["sign"] == "Рак"


async def test_pending_today_after_sign_returns_letter(
    session: FakeSession,
    user: FakeUser,
    dialog: FakeDialog,
    patched_storage: FakeDialog,
    logged: list[dict],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert patched_storage is dialog
    dialog.state = states.ASK_SIGN
    dialog.data = {"pending": "today"}
    letter = "Рак\nДень спокойный."

    async def fake_letters(_session: Any, _day: Any) -> dict[str, str]:
        return {"Рак": letter}

    monkeypatch.setattr(menu.broadcast, "letters_for", fake_letters)
    replies = await _handle(
        session, user, _incoming("", payload={"cmd": "set_sign", "sign": "Рак"})
    )
    assert user.sun_sign == "Рак"
    assert replies[0].text == texts.SIGN_SAVED.format(sign="Рак")
    assert replies[1].text == letter
    assert logged[0]["sign"] == "Рак"


async def test_set_sign_does_not_subscribe(
    session: FakeSession,
    user: FakeUser,
    dialog: FakeDialog,
    patched_storage: FakeDialog,
    logged: list[dict],
) -> None:
    assert patched_storage is dialog
    assert user.subscribed is False
    await _handle(
        session, user, _incoming("", payload={"cmd": "set_sign", "sign": "Дева"})
    )
    assert user.subscribed is False
    assert user.sun_sign == "Дева"
    assert logged[0]["sign"] == "Дева"


def test_menu_is_bottom_keyboard(user: FakeUser) -> None:
    # Inline-копия под сообщением дублировала нижнюю клавиатуру (04.11.2026).
    reply = menu.menu_reply(user)
    assert reply.inline is False
    assert [button.label for button in reply.buttons[0]] == [texts.BTN_TODAY, texts.BTN_MONTH]


async def test_manual_sign_beats_chart(
    session: FakeSession, user: FakeUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def chart_leo(*args: Any, **kwargs: Any) -> Any:
        return SimpleNamespace(calc={"natal": {"planets": [{"name": "Sun", "sign_ru": "Лев"}]}})

    monkeypatch.setattr(menu.charts_service, "get_chart", chart_leo)
    user.primary_chart_id = 85
    assert await menu._resolve_sign(session, user) == "Лев"
    user.sun_sign = "Рак"
    assert await menu._resolve_sign(session, user) == "Рак"
