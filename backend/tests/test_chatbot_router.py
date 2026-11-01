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
    pass


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
    assert replies[0].text == texts.NATAL_ASK_DATE


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
    assert len(replies[0].buttons) == 3


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
