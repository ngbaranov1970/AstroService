"""Проверки короткого пути к рассылке (app/api/routes/vk.py, POST /vk/sun-sign).

Базы и сети нет: пользователь, сессия и событие подменены. Смысл — знак
пишется только вне дня смены знака, а отказ в этот день не стирает
записанный раньше.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api.deps import current_user
from app.api.routes import vk as vk_routes
from app.db import get_session
from app.main import app
from app.models import Platform
from app.services import events

URL = "/api/v1/vk/sun-sign"

# 22.06.1985 — день смены знака (проверен в test_transits.py), 11.05.1990 — нет.
CUSP_DAY = "1985-06-22"
PLAIN_DAY = "1990-05-11"


@dataclass
class FakeUser:
    id: int = 1
    platform: Platform = Platform.VK
    sun_sign: str | None = None


class FakeSession:
    """Сессия-пустышка: маршруту от неё нужен только commit."""

    async def commit(self) -> None:
        pass


@pytest.fixture
def user() -> FakeUser:
    return FakeUser()


@pytest.fixture
def logged(monkeypatch: pytest.MonkeyPatch, user: FakeUser) -> list[dict]:
    """Подменить пользователя и сессию, события собрать в список."""
    calls: list[dict] = []

    async def fake_log(session, name, **props):
        calls.append({"name": name, **props})

    async def fake_session():
        yield FakeSession()

    monkeypatch.setattr(vk_routes.events, "log", fake_log)
    app.dependency_overrides[get_session] = fake_session
    app.dependency_overrides[current_user] = lambda: user
    yield calls
    app.dependency_overrides.clear()


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


def test_plain_day_stores_sign(client: TestClient, logged: list, user: FakeUser) -> None:
    response = client.post(URL, json={"birth_date": PLAIN_DAY})

    assert response.status_code == 200
    assert response.json() == {"sign": "Телец"}
    assert user.sun_sign == "Телец"
    assert logged == [{"name": events.SUN_SIGN_ENTERED, "user_id": 1, "sign": "Телец"}]


def test_cusp_day_refuses_and_keeps_old_sign(
    client: TestClient, logged: list, user: FakeUser
) -> None:
    user.sun_sign = "Лев"

    response = client.post(URL, json={"birth_date": CUSP_DAY})

    assert response.status_code == 200
    assert response.json() == {"sign": None}
    assert user.sun_sign == "Лев"
    assert logged == [{"name": events.SUN_SIGN_ENTERED, "user_id": 1, "cusp": True}]


def test_web_user_is_rejected(client: TestClient, logged: list, user: FakeUser) -> None:
    user.platform = Platform.WEB

    response = client.post(URL, json={"birth_date": PLAIN_DAY})

    assert response.status_code == 409
    assert user.sun_sign is None
    assert logged == []


def test_future_date_is_rejected(client: TestClient, logged: list, user: FakeUser) -> None:
    # Два дня вперёд: сервер сверяет с сегодняшней датой по UTC
    future = (datetime.now(UTC).date() + timedelta(days=2)).isoformat()

    response = client.post(URL, json={"birth_date": future})

    assert response.status_code == 422
    assert user.sun_sign is None
