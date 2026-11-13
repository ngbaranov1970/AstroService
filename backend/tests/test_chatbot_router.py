"""Ядро chatbot.router: кодовые слова, сброс и меню без VK и БД."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.chatbot import router, states, texts
from app.chatbot.types import AppButton, Incoming, Reply
from app.models import User


@dataclass
class FakeUser:
    id: int = 1
    subscribed: bool = False


@dataclass
class FakeDialog:
    user_id: int = 1
    state: str = states.IDLE
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class FakeSession:
    added: list[Any] = field(default_factory=list)

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
    """Диалог в памяти: load отдаёт его, save/reset меняют поля."""

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
    return dialog


def _incoming(text: str, *, payload: dict[str, Any] | None = None) -> Incoming:
    return Incoming(platform="vk", external_id="100", text=text, payload=payload)


async def _handle(
    session: FakeSession, user: FakeUser, incoming: Incoming
) -> list[Reply]:
    # Фейки duck-typing'ом совпадают с тем, что читает handle; cast — для IDE.
    return await router.handle(
        cast(AsyncSession, session),
        cast(User, user),
        incoming,
    )


def test_router_does_not_import_app_vk() -> None:
    """В исходнике router.py нет импортов из app.vk (граница слоёв)."""
    assert router.__file__ is not None
    tree = ast.parse(Path(router.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("app.vk"), node.module
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("app.vk"), alias.name


@pytest.mark.parametrize(
    "text",
    ["КАРТА", "карта ", "Карта", "кАрта", "  карта  "],
)
async def test_karta_starts_natal(
    text: str,
    session: FakeSession,
    user: FakeUser,
    dialog: FakeDialog,
    patched_storage: FakeDialog,
) -> None:
    assert patched_storage is dialog
    replies = await _handle(session, user, _incoming(text))
    assert dialog.state == states.NATAL_DATE
    assert len(replies) == 1
    assert isinstance(replies[0], Reply)
    assert replies[0].text == texts.NATAL_ASK_ALL


@pytest.mark.parametrize("text", ["Пара", "пара", "ПАРА", " пара "])
async def test_para_opens_pair_branch(
    text: str,
    session: FakeSession,
    user: FakeUser,
    dialog: FakeDialog,
    patched_storage: FakeDialog,
) -> None:
    assert patched_storage is dialog
    replies = await _handle(session, user, _incoming(text))
    assert dialog.state == states.IDLE
    assert len(replies) == 1
    assert replies[0].text == texts.PAIR_TEXT
    assert replies[0].inline is True
    assert len(replies[0].buttons) == 1
    button = replies[0].buttons[0][0]
    assert isinstance(button, AppButton)
    assert button.hash == "pair"


async def test_unknown_text_in_idle_shows_menu(
    session: FakeSession,
    user: FakeUser,
    dialog: FakeDialog,
    patched_storage: FakeDialog,
) -> None:
    assert patched_storage is dialog
    replies = await _handle(session, user, _incoming("привет"))
    assert dialog.state == states.IDLE
    assert len(replies) == 1
    assert replies[0].text == texts.MENU_TEXT
    assert len(replies[0].buttons) == 4


async def test_menu_text_resets_state(
    session: FakeSession,
    user: FakeUser,
    dialog: FakeDialog,
    patched_storage: FakeDialog,
) -> None:
    assert patched_storage is dialog
    dialog.state = states.NATAL_DATE
    dialog.data = {"draft": True}
    replies = await _handle(session, user, _incoming("Меню"))
    assert dialog.state == states.IDLE
    assert dialog.data == {}
    assert replies[0].text == texts.MENU_TEXT


async def test_start_payload_resets_state(
    session: FakeSession,
    user: FakeUser,
    dialog: FakeDialog,
    patched_storage: FakeDialog,
) -> None:
    assert patched_storage is dialog
    dialog.state = states.NATAL_WAIT
    replies = await _handle(
        session, user, _incoming("", payload={"command": "start"})
    )
    assert dialog.state == states.IDLE
    assert replies[0].text == texts.MENU_TEXT


async def test_normalize_yo_and_spaces() -> None:
    assert router.normalize("  КАРТЁ  ") == "карте"
    assert router.normalize("пара\t\n") == "пара"


# --- вход только подписчикам (flows/gate.py) ---------------------------------


def _member(*answers: bool | None):
    """Проверка подписки, отдающая ответы по очереди; calls — сколько раз спросили."""
    queue = list(answers)

    async def check() -> bool | None:
        check.calls += 1
        return queue.pop(0)

    check.calls = 0
    return check


async def _gated(session, user, incoming, check) -> list[Reply]:
    return await router.handle(
        cast(AsyncSession, session), cast(User, user), incoming, is_member=check
    )


def _event_names(session: FakeSession) -> list[str]:
    return [e.name for e in session.added if hasattr(e, "name")]


@pytest.mark.parametrize(
    ("incoming", "text", "target"),
    [
        (_incoming("КАРТА"), texts.GATE_NATAL, "natal"),
        (_incoming("", payload={"cmd": "natal"}), texts.GATE_NATAL, "natal"),
        (_incoming("9.03.1987, 14:30, Егорьевск"), texts.GATE_NATAL, "natal"),
        (_incoming("пара"), texts.GATE_PAIR, "pair"),
        (_incoming("", payload={"cmd": "pair"}), texts.GATE_PAIR, "pair"),
    ],
)
async def test_gate_stops_non_member_before_questions(
    incoming: Incoming,
    text: str,
    target: str,
    session: FakeSession,
    user: FakeUser,
    dialog: FakeDialog,
    patched_storage: FakeDialog,
) -> None:
    [reply] = await _gated(session, user, incoming, _member(False))
    assert reply.text == text
    link, check = [row[0] for row in reply.buttons]
    assert link.url == texts.GROUP_URL
    assert check.payload == {"cmd": "gate_check", "target": target}
    assert dialog.state == states.IDLE
    assert _event_names(session) == ["gate_shown"]


@pytest.mark.parametrize("answer", [True, None])
async def test_gate_lets_member_and_unknown_through(
    answer: bool | None,
    session: FakeSession,
    user: FakeUser,
    dialog: FakeDialog,
    patched_storage: FakeDialog,
) -> None:
    [reply] = await _gated(session, user, _incoming("КАРТА"), _member(answer))
    assert reply.text == texts.NATAL_ASK_ALL
    assert "gate_shown" not in _event_names(session)
    assert "gate_passed" not in _event_names(session)


async def test_gate_check_not_yet_keeps_data_and_does_not_log_again(
    session: FakeSession,
    user: FakeUser,
    dialog: FakeDialog,
    patched_storage: FakeDialog,
) -> None:
    check = _member(False, False)
    await _gated(session, user, _incoming("9.03.1987, 14:30, Егорьевск"), check)
    [reply] = await _gated(
        session, user, _incoming("", payload={"cmd": "gate_check", "target": "natal"}), check
    )
    assert reply.text == texts.GATE_NOT_YET
    assert len(reply.buttons) == 2
    assert dialog.data["gate"]["text"] == "9.03.1987, 14:30, Егорьевск"
    assert _event_names(session) == ["gate_shown"]


async def test_gate_check_passed_continues_with_saved_text(
    session: FakeSession,
    user: FakeUser,
    dialog: FakeDialog,
    patched_storage: FakeDialog,
) -> None:
    # Время не указано: сбор должен продолжиться с вопроса о времени, а не с даты.
    check = _member(False, True)
    await _gated(session, user, _incoming("9.03.1987"), check)
    replies = await _gated(
        session, user, _incoming("", payload={"cmd": "gate_check", "target": "natal"}), check
    )
    assert check.calls == 2
    assert dialog.state == states.NATAL_TIME
    assert dialog.data["date"] == "1987-03-09"
    assert "gate" not in dialog.data
    assert replies[0].text.startswith("9 марта 1987")
    assert _event_names(session)[:2] == ["gate_shown", "gate_passed"]


async def test_gate_check_passed_opens_pair(
    session: FakeSession,
    user: FakeUser,
    dialog: FakeDialog,
    patched_storage: FakeDialog,
) -> None:
    check = _member(False, True)
    await _gated(session, user, _incoming("пара"), check)
    [reply] = await _gated(
        session, user, _incoming("", payload={"cmd": "gate_check", "target": "pair"}), check
    )
    assert reply.text == texts.PAIR_TEXT
    assert _event_names(session) == ["gate_shown", "gate_passed", "bot_pair_link"]


async def test_gate_does_not_touch_vip(
    session: FakeSession,
    user: FakeUser,
    dialog: FakeDialog,
    patched_storage: FakeDialog,
) -> None:
    check = _member()
    await _gated(session, user, _incoming("", payload={"cmd": "vip"}), check)
    assert check.calls == 0
