"""VK-адаптер и Callback: фон, повторы, клавиатуры и отправка без сети и БД."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import BackgroundTasks
from pydantic import SecretStr
from starlette.requests import Request

from app.chatbot.types import AppButton, Button, Job, LinkButton, Reply
from app.config import Settings
from app.models import Platform, User
from app.services import users
from app.vk import api, bot
from app.web.routes import vk_callback as callback


@pytest.mark.parametrize("text", ["а" * 9000, ("слово " * 2000), ("абзац\n\n" * 1500)])
def test_split_long_text(text):
    parts = bot.split_text(text)
    assert len(parts) >= 3
    assert all(0 < len(part) <= 4000 for part in parts)
    assert "".join("".join(parts).split()) == "".join(text.split())


def test_split_prefers_paragraph_then_line_then_word():
    assert bot.split_text("абзац\n\nстрока слово", 13) == ["абзац", "строка слово"]
    assert bot.split_text("строка\nслово слово", 13) == ["строка", "слово слово"]
    assert bot.split_text("слово слово слово", 12) == ["слово слово", "слово"]
    assert bot.split_text(" \n ") == []
    with pytest.raises(ValueError):
        bot.split_text("текст", 0)


def test_strip_markdown():
    assert bot.strip_markdown("## Заголовок\n**Жирный** текст\n### Ещё\nТело") == (
        "Заголовок\n\nЖирный текст\n\nЕщё\n\nТело"
    )


def test_keyboard_all_button_types(monkeypatch):
    monkeypatch.setattr(
        bot,
        "get_settings",
        lambda: SimpleNamespace(
            vk_app_id=123,
            vk_group_id=456,
        ),
    )
    reply = Reply(
        "Меню",
        buttons=[
            [Button("Карта", {"cmd": "natal"})],
            [LinkButton("Сайт", "https://example.com")],
            [AppButton("Пара", "pair")],
        ],
    )
    keyboard = json.loads(bot.keyboard_json(reply))
    assert keyboard["one_time"] is False
    assert keyboard["inline"] is False
    text, link, app = [row[0] for row in keyboard["buttons"]]
    assert text["color"] == "secondary"
    assert json.loads(text["action"]["payload"]) == {"cmd": "natal"}
    assert link == {
        "action": {
            "type": "open_link",
            "label": "Сайт",
            "link": "https://example.com",
        }
    }
    assert app["action"] == {
        "type": "open_app",
        "label": "Пара",
        "app_id": 123,
        "owner_id": -456,
        "hash": "pair",
    }
    inline = json.loads(bot.keyboard_json(Reply("Текст", buttons=reply.buttons, inline=True)))
    assert inline["inline"] is True
    assert "one_time" not in inline
    assert bot.keyboard_json(Reply("Без кнопок")) is None


@pytest.mark.parametrize("inline,rows,width", [(False, 11, 1), (True, 7, 1), (False, 1, 6)])
def test_keyboard_rejects_oversize(inline, rows, width):
    reply = Reply("Текст", [[Button("Кнопка", {})] * width] * rows, inline)
    with pytest.raises(ValueError):
        bot.keyboard_json(reply)


def test_keyboard_rejects_inline_over_10_buttons():
    # Рядов мало, но кнопок 12 — VK отвечает 911.
    reply = Reply("Текст", [[Button("К", {})] * 3] * 4, inline=True)
    with pytest.raises(ValueError, match="10"):
        bot.keyboard_json(reply)


async def test_send_message_uses_existing_transport(monkeypatch):
    result = api.SendResult(user_id=100, message_id=42)
    send = AsyncMock(return_value=[result])
    monkeypatch.setattr(api, "_send_chunk", send)
    assert await api.send_message(100, "ответ", random_id=12, keyboard="{}") is result
    send.assert_awaited_once_with([100], "ответ", 12, "{}")


@pytest.fixture
def session():
    value = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock(), added=[])
    value.add = value.added.append
    return value


@pytest.fixture
def adapter(monkeypatch, session):
    class SessionContext:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(bot, "get_sessionmaker", lambda: SessionContext)
    user = User(id=7, platform=Platform.VK, external_id="100", subscribed=False)
    create = AsyncMock(return_value=user)
    handle = AsyncMock(
        return_value=[Reply("## Ответ\n" + "а" * 9000, [[Button("Меню", {"cmd": "menu"})]])]
    )
    send = AsyncMock(return_value=api.SendResult(user_id=100, message_id=42))
    monkeypatch.setattr(bot.users, "get_or_create", create)
    monkeypatch.setattr(bot.router, "handle", handle)
    monkeypatch.setattr(bot.api, "send_message", send)
    return SimpleNamespace(create=create, handle=handle, send=send)


def message():
    return {
        "message": {
            "from_id": 100,
            "peer_id": 100,
            "conversation_message_id": 55,
            "text": "КАРТА",
            "payload": '{"cmd":"natal"}',
            "ref": "karta_1",
        }
    }


async def test_process_message_parses_and_sends_parts(adapter, session):
    await bot.process_message(message())
    incoming = adapter.handle.await_args.args[2]
    assert incoming.platform == "vk"
    assert incoming.external_id == "100"
    assert incoming.payload == {"cmd": "natal"}
    assert incoming.ref == "karta_1"
    [event] = session.added
    assert event.name == bot.events.BOT_REF
    assert event.props == {"ref": "karta_1", "ref_source": None}
    assert adapter.create.await_args.kwargs["subscribed"] is False
    session.commit.assert_awaited_once()
    calls = adapter.send.await_args_list
    assert len(calls) >= 3
    assert all(len(call.args[1]) <= 4000 for call in calls)
    assert all(call.kwargs["keyboard"] is None for call in calls[:-1])
    assert calls[-1].kwargs["keyboard"] is not None
    ids = [call.kwargs["random_id"] for call in calls]
    assert len(set(ids)) == len(ids)
    adapter.send.reset_mock()
    await bot.process_message(message())
    assert ids == [call.kwargs["random_id"] for call in adapter.send.await_args_list]


@pytest.mark.parametrize("payload", ["не JSON", "[]", "null", None])
async def test_invalid_payload_is_ignored(adapter, payload):
    obj = message()
    obj["message"]["payload"] = payload
    await bot.process_message(obj)
    assert adapter.handle.await_args.args[2].payload is None


@pytest.mark.parametrize("changes", [{"out": 1}, {"peer_id": 2000000001}, {"from_id": -1}])
async def test_adapter_ignores_outgoing_and_group_chats(adapter, changes):
    obj = message()
    obj["message"].update(changes)
    await bot.process_message(obj)
    adapter.handle.assert_not_awaited()
    adapter.send.assert_not_awaited()


async def test_send_refusal_stops_remaining_parts(adapter):
    adapter.send.return_value = api.SendResult(user_id=100, error_code=901)
    await bot.process_message(message())
    adapter.send.assert_awaited_once()


async def test_job_runs_after_first_replies_in_new_session(adapter, monkeypatch, session):
    """«Считаю» уходит до долгой работы, разбор — после неё, random_id не повторяются."""
    adapter.handle.return_value = [Reply("Считаю"), Job("natal_report")]
    order: list[str] = []

    async def send(peer_id, text, **kwargs):
        order.append(text)
        return api.SendResult(user_id=100, message_id=1)

    async def run_job(_session, user, job):
        order.append(f"job:{job.name}:{user.id}")
        return [Reply("Разбор")]

    adapter.send.side_effect = send
    monkeypatch.setattr(bot.users, "get_by_id", AsyncMock(return_value=User(id=7)))
    monkeypatch.setattr(bot.router, "run_job", run_job)
    await bot.process_message(message())
    assert order == ["Считаю", "job:natal_report:7", "Разбор"]
    ids = [call.kwargs["random_id"] for call in adapter.send.await_args_list]
    assert len(set(ids)) == 2
    assert session.commit.await_count == 2


async def test_background_error_does_not_log_message(adapter, caplog):
    adapter.handle.side_effect = RuntimeError("секретный текст рождения")
    await bot.process_message(message())
    assert "RuntimeError" in caplog.text
    assert "секретный текст" not in caplog.text
    adapter.send.assert_not_awaited()


@pytest.fixture
def callback_setup(monkeypatch):
    settings = SimpleNamespace(
        vk_callback_enabled=True,
        vk_bot_enabled=True,
        vk_group_id=456,
        vk_callback_secret=SecretStr("secret"),
        vk_callback_confirmation="confirmed",
    )
    monkeypatch.setattr(callback, "get_settings", lambda: settings)
    monkeypatch.setattr(callback.users, "get_or_create", AsyncMock(return_value=User(id=7)))
    monkeypatch.setattr(callback.users, "fill_vk_name", AsyncMock())
    monkeypatch.setattr(callback.users, "mark_vk_messages_allowed", lambda user: False)
    monkeypatch.setattr(callback.users, "clear_delivery_block", lambda user: False)
    monkeypatch.setattr(callback.events, "log", AsyncMock())
    monkeypatch.setattr(callback.storage, "seen", AsyncMock(return_value=False))
    return settings


async def invoke_callback(session, **changes):
    body = {
        "type": "message_new",
        "group_id": 456,
        "secret": "secret",
        "event_id": "event-1",
        "object": message(),
    }
    body.update(changes)

    async def receive():
        return {"type": "http.request", "body": json.dumps(body).encode()}

    request = Request({"type": "http", "method": "POST", "path": "/vk/callback"}, receive)
    background = BackgroundTasks()
    response = await callback.vk_callback(request, session, background)
    return response, background


async def test_callback_schedules_only_after_commit(callback_setup, session, monkeypatch):
    process = AsyncMock()
    monkeypatch.setattr(callback.vk_bot, "process_message", process)
    response, background = await invoke_callback(session)
    assert response.body == b"ok"
    session.commit.assert_awaited_once()
    process.assert_not_awaited()
    assert len(background.tasks) == 1
    await background()
    process.assert_awaited_once_with(message())
    assert callback.users.get_or_create.await_args.kwargs["subscribed"] is False


async def test_callback_disabled_keeps_old_behavior(callback_setup, session):
    callback_setup.vk_bot_enabled = False
    response, background = await invoke_callback(session)
    assert response.body == b"ok"
    assert background.tasks == []
    callback.storage.seen.assert_not_awaited()
    assert "subscribed" not in callback.users.get_or_create.await_args.kwargs


async def test_callback_duplicate_does_not_schedule(callback_setup, session):
    callback.storage.seen.return_value = True
    response, background = await invoke_callback(session)
    assert response.body == b"ok"
    assert background.tasks == []


@pytest.mark.parametrize("event_id", [None, "", "x" * 65])
async def test_callback_missing_event_id_is_not_dispatched(callback_setup, session, event_id):
    response, background = await invoke_callback(session, event_id=event_id)
    assert response.body == b"ok"
    assert background.tasks == []
    callback.storage.seen.assert_not_awaited()


async def test_callback_bad_secret_does_not_dispatch(callback_setup, session):
    response, background = await invoke_callback(session, secret="wrong")
    assert response.status_code == 403
    assert background.tasks == []
    session.commit.assert_not_awaited()


async def test_existing_user_subscription_is_unchanged(monkeypatch, session):
    user = User(id=7, subscribed=True)
    monkeypatch.setattr(users, "_find", AsyncMock(return_value=user))
    result = await users.get_or_create(
        session,
        platform=Platform.VK,
        external_id="100",
        subscribed=False,
    )
    assert result is user
    assert result.subscribed is True


async def test_new_bot_user_is_inserted_without_subscription(monkeypatch, session):
    monkeypatch.setattr(users, "_find", AsyncMock(return_value=None))
    user = User(id=7, subscribed=False)
    session.scalars = AsyncMock(return_value=Mock(first=Mock(return_value=user)))
    assert (
        await users.get_or_create(
            session,
            platform=Platform.VK,
            external_id="100",
            subscribed=False,
        )
        is user
    )
    statement = session.scalars.await_args.args[0]
    assert statement.compile().params["subscribed"] is False


async def test_message_allow_does_not_subscribe_or_start_bot(callback_setup, session):
    response, background = await invoke_callback(
        session,
        type="message_allow",
        object={"user_id": 100},
    )
    assert response.body == b"ok"
    assert background.tasks == []
    assert callback.users.get_or_create.await_args.kwargs["subscribed"] is False
    callback.storage.seen.assert_not_awaited()


def test_bot_disabled_by_default(monkeypatch):
    monkeypatch.delenv("VK_BOT_ENABLED", raising=False)
    settings = Settings(_env_file=None, deepseek_api_key="test", database_url="test", debug=False)
    assert settings.vk_bot_enabled is False
