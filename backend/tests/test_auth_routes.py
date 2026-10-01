"""Проверки входа из Mini App (app/api/routes/auth.py, POST /auth/vk).

Базы и сети нет: проверка подписи, запись пользователя и событие подменены.
Смысл — метка плитки меню сообщества в app_opened: из белого списка она
пишется как есть, мусор и отсутствие — как None, и вход при этом не падает.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.routes import auth as auth_routes
from app.db import get_session
from app.main import app
from app.services import events

URL = "/api/v1/auth/vk"
PARAMS = {"vk_user_id": "182907601", "vk_ref": "group", "vk_platform": "mobile_android"}


@dataclass
class FakeUser:
    id: int = 1
    name: str | None = None
    is_tester: bool = False


class FakeSession:
    """Сессия-пустышка: маршруту от неё нужен только commit."""

    async def commit(self) -> None:
        pass


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Подменить всё, что ходит наружу, и записать событие в словарь."""
    calls: dict = {}

    async def fake_get_or_create(session, **kwargs):
        return FakeUser()

    async def fake_log(session, name, **props):
        calls["event"] = {"name": name, **props}

    async def fake_fill_vk_name(user):
        pass

    # Подпись не проверяем: её ветки — забота auth.verify_vk_launch
    monkeypatch.setattr(auth_routes, "get_settings", lambda: SimpleNamespace(vk_enabled=True))
    monkeypatch.setattr(auth_routes.auth, "verify_vk_launch", lambda params: PARAMS["vk_user_id"])
    monkeypatch.setattr(auth_routes.auth, "issue_session", lambda user_id, surface: "jwt")
    monkeypatch.setattr(auth_routes.users_service, "get_or_create", fake_get_or_create)
    monkeypatch.setattr(auth_routes.users_service, "fill_vk_name", fake_fill_vk_name)
    monkeypatch.setattr(auth_routes.events, "log", fake_log)

    async def fake_session():
        yield FakeSession()

    app.dependency_overrides[get_session] = fake_session
    yield calls
    app.dependency_overrides.clear()


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize("entry", sorted(events.ENTRY_GOALS))
def test_entry_from_whitelist_is_logged(client: TestClient, recorded: dict, entry: str) -> None:
    response = client.post(URL, json={"params": PARAMS, "entry": entry})

    assert response.status_code == 200
    assert recorded["event"]["name"] == events.APP_OPENED
    assert recorded["event"]["entry"] == entry


@pytest.mark.parametrize("entry", ["https://evil.example", "NATAL", "", "x" * 500])
def test_unknown_entry_is_dropped(client: TestClient, recorded: dict, entry: str) -> None:
    # Мусор в метке не ломает вход и не попадает в props
    response = client.post(URL, json={"params": PARAMS, "entry": entry})

    assert response.status_code == 200
    assert recorded["event"]["entry"] is None


def test_missing_entry_keeps_old_behaviour(client: TestClient, recorded: dict) -> None:
    # Старый фронт поле не шлёт — вход и прочие метки те же, что были
    response = client.post(URL, json={"params": PARAMS})

    assert response.status_code == 200
    assert recorded["event"]["entry"] is None
    assert recorded["event"]["ref"] == "group"
    assert recorded["event"]["platform"] == "mobile_android"
