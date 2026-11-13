"""КАРТА/ПАРА в комментариях, ветка wall_reply_new, концовка поста и бот на воронке.

Без БД и сети: сессия, VK API и ядро подменены. Главное — комментарий не даёт
разрешения писать, а владелец получает одно уведомление на человека и пост.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks
from pydantic import SecretStr
from starlette.requests import Request

from app.chatbot.types import Reply
from app.llm.prompts import promo
from app.models import Platform, User
from app.services import admin, events
from app.vk import api, comments
from app.web.routes import vk_callback as callback

GROUP_ID = 456


def comment(**changes):
    obj = {
        "id": 900, "from_id": 100, "post_id": 31, "owner_id": -GROUP_ID, "text": "Карта",
    }
    obj.update(changes)
    return obj


@pytest.fixture
def settings(monkeypatch):
    value = SimpleNamespace(
        vk_callback_enabled=True,
        vk_bot_enabled=True,
        vk_group_id=GROUP_ID,
        vk_callback_secret=SecretStr("secret"),
        vk_callback_confirmation="confirmed",
        vip_owner_vk_id=1,
    )
    monkeypatch.setattr(comments, "get_settings", lambda: value)
    monkeypatch.setattr(callback, "get_settings", lambda: value)
    return value


# --- разбор комментария -----------------------------------------------------


@pytest.mark.parametrize(
    "text,code",
    [
        ("КАРТА", "КАРТА"), (" карта ", "КАРТА"), ("Карта 9.03.1987 Егорьевск", "КАРТА"),
        ("пара", "ПАРА"), ("картами", None), ("пара слов", None), ("красиво", None),
    ],
)
def test_code_word(text, code):
    assert comments.code_word(text) == code


@pytest.mark.parametrize(
    "changes", [{"from_id": -GROUP_ID}, {"owner_id": -999}, {"text": "спасибо"}, {"post_id": None}]
)
def test_parse_comment_skips(settings, changes):
    assert comments.parse_comment(comment(**changes)) is None


def test_parse_comment_keeps_code(settings):
    parsed = comments.parse_comment(comment())
    assert (parsed["from_id"], parsed["post_id"], parsed["code"]) == (100, 31, "КАРТА")


# --- process_comment --------------------------------------------------------


@pytest.fixture
def flow(monkeypatch, settings):
    session = SimpleNamespace(commit=AsyncMock(), added=[])
    session.add = session.added.append

    class SessionContext:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *args):
            return False

    user = User(id=7, platform=Platform.VK, external_id="100", platform_username="Анна К")
    monkeypatch.setattr(comments, "get_sessionmaker", lambda: SessionContext)
    monkeypatch.setattr(comments.users, "get_or_create", AsyncMock(return_value=user))
    monkeypatch.setattr(comments.users, "fill_vk_name", AsyncMock())
    monkeypatch.setattr(comments.router, "handle", AsyncMock(return_value=[Reply("Дата?")]))
    monkeypatch.setattr(comments.vk_bot, "deliver", AsyncMock())
    monkeypatch.setattr(comments, "_owner_notified", AsyncMock(return_value=False))
    send = AsyncMock(return_value=api.SendResult(user_id=1, message_id=5))
    monkeypatch.setattr(comments.api, "send_message", send)
    return SimpleNamespace(session=session, user=user, send=send)


def names(session):
    return [event.name for event in session.added]


async def test_allowed_user_gets_dm(flow):
    flow.user.vk_messages_allowed_at = datetime.now(UTC)
    await comments.process_comment(comment())

    incoming = comments.router.handle.await_args.args[2]
    assert incoming.text == "Карта"
    comments.vk_bot.deliver.assert_awaited_once_with(
        100, "c900", [Reply(comments.TO_DM_TEXT), Reply("Дата?")], 7
    )
    assert names(flow.session) == [events.BOT_COMMENT_TO_DM]
    assert flow.session.added[0].props == {"post_id": 31, "code": "КАРТА"}
    flow.send.assert_not_awaited()
    assert comments.users.get_or_create.await_args.kwargs["subscribed"] is False


async def test_blocked_user_goes_to_owner(flow):
    flow.user.vk_messages_allowed_at = datetime.now(UTC)
    flow.user.bot_blocked_at = datetime.now(UTC)
    await comments.process_comment(comment())
    comments.vk_bot.deliver.assert_not_awaited()
    flow.send.assert_awaited_once()


async def test_not_allowed_notifies_owner_once(flow):
    await comments.process_comment(comment(text="пара"))

    comments.router.handle.assert_not_awaited()
    owner_id, text = flow.send.await_args.args
    assert owner_id == 1
    assert "Анна К" in text and "ПАРА" in text
    assert f"vk.ru/wall-{GROUP_ID}_31?reply=900" in text
    assert names(flow.session) == [events.BOT_COMMENT_NO_DM]
    # Разрешение писать комментарий не ставит.
    assert flow.user.vk_messages_allowed_at is None


async def test_already_notified_is_silent(flow):
    comments._owner_notified.return_value = True
    await comments.process_comment(comment())
    flow.send.assert_not_awaited()
    assert names(flow.session) == []


async def test_owner_refusal_is_not_counted(flow):
    flow.send.return_value = api.SendResult(user_id=1, error_code=901)
    await comments.process_comment(comment())
    assert names(flow.session) == []


async def test_error_does_not_log_comment_text(flow, caplog):
    comments.users.get_or_create.side_effect = RuntimeError("9.03.1987 Егорьевск")
    await comments.process_comment(comment(text="КАРТА 9.03.1987 Егорьевск"))
    assert "RuntimeError" in caplog.text
    assert "Егорьевск" not in caplog.text


# --- Callback: ветка wall_reply_new -----------------------------------------


@pytest.fixture
def route(monkeypatch, settings):
    def forbidden(*args, **kwargs):
        raise AssertionError("комментарий не должен трогать пользователя и разрешения")

    monkeypatch.setattr(callback.users, "get_or_create", forbidden)
    monkeypatch.setattr(callback.users, "mark_vk_messages_allowed", forbidden)
    monkeypatch.setattr(callback.storage, "seen", AsyncMock(return_value=False))
    monkeypatch.setattr(callback.comments, "process_comment", AsyncMock())
    return SimpleNamespace(commit=AsyncMock())


async def invoke(session, obj):
    body = {
        "type": "wall_reply_new", "group_id": GROUP_ID, "secret": "secret",
        "event_id": "event-1", "object": obj,
    }

    async def receive():
        return {"type": "http.request", "body": json.dumps(body).encode()}

    request = Request({"type": "http", "method": "POST", "path": "/vk/callback"}, receive)
    background = BackgroundTasks()
    response = await callback.vk_callback(request, session, background)
    return response, background


async def test_callback_schedules_comment(route):
    response, background = await invoke(route, comment())
    assert response.body == b"ok"
    route.commit.assert_awaited_once()
    assert len(background.tasks) == 1
    await background()
    callback.comments.process_comment.assert_awaited_once_with(comment())


async def test_callback_ignores_plain_comment(route):
    response, background = await invoke(route, comment(text="спасибо"))
    assert response.body == b"ok"
    assert background.tasks == []
    callback.storage.seen.assert_not_awaited()


async def test_callback_comment_disabled(route, settings):
    settings.vk_bot_enabled = False
    response, background = await invoke(route, comment())
    assert response.body == b"ok"
    assert background.tasks == []


async def test_callback_duplicate_comment(route):
    callback.storage.seen.return_value = True
    _, background = await invoke(route, comment())
    assert background.tasks == []


# --- концовка вечернего поста -----------------------------------------------


def test_footer_with_bot(monkeypatch):
    monkeypatch.setattr(promo, "get_settings", lambda: SimpleNamespace(vk_bot_enabled=True))
    footer = promo.sign_lines_footer(date(2026, 11, 5))
    assert footer.startswith(promo.SIGN_LINES_FOOTER)
    assert footer.endswith(f"{promo.BOT_LINE}\n👉 vk.me/astrozvezdochet?ref=evening_20261105")


def test_footer_without_bot(monkeypatch):
    monkeypatch.setattr(promo, "get_settings", lambda: SimpleNamespace(vk_bot_enabled=False))
    assert promo.sign_lines_footer(date(2026, 11, 5)) == promo.SIGN_LINES_FOOTER


# --- бот на воронке ---------------------------------------------------------


async def test_bot_stats_weeks_and_refs():
    mon = datetime(2026, 11, 2, 12, tzinfo=UTC)
    prev = datetime(2026, 10, 28, 12, tzinfo=UTC)
    free = {"type": "free_natal"}
    rows = [
        (events.GROUP_MESSAGES_ALLOWED, 1, mon, {}),
        (events.GROUP_MESSAGES_ALLOWED, 1, mon, {}),
        (events.GROUP_MESSAGES_ALLOWED, 2, prev, {}),
        (events.BOT_NATAL_STARTED, 1, mon, {}),
        (events.REPORT_READY, 1, mon, free),
        # Разбор из Mini App: в боте КАРТУ не начинал — в столбец бота не идёт.
        (events.REPORT_READY, 3, mon, free),
        (events.BOT_REF, 1, mon, {"ref": "evening_20261105"}),
        (events.BOT_REF, 2, prev, {"ref": "evening_20261105"}),
        (events.BOT_REF, 3, mon, {"ref": "ads"}),
    ]
    result = [SimpleNamespace(name=n, user_id=u, ts=t, props=p) for n, u, t, p in rows]
    session = SimpleNamespace(execute=AsyncMock(return_value=result))

    stats = await admin.bot_stats(session, since=prev, until=mon)

    by_title = lambda week: dict(zip(stats.titles, week.counts, strict=True))
    current, previous = stats.weeks
    assert (current.start, previous.start) == (date(2026, 11, 2), date(2026, 10, 26))
    assert by_title(current)["Разрешили писать"] == 1
    assert by_title(current)["КАРТА готова"] == 1
    assert by_title(previous)["Разрешили писать"] == 1
    assert stats.refs == [("evening_20261105", 2), ("ads", 1)]
