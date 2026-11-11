"""Вход в админку: форма, cookie-сессия, запасной Basic, выход."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.services import auth
from app.web import deps
from app.web.routes import (
    admin_login,
    legal,  # noqa: F401 — кладёт `merchant` в шаблоны, как в app.main
)

# Мини-приложение вместо app.main: настоящие страницы админки ходят в базу,
# а проверяем здесь только охрану.
_protected = APIRouter(prefix="/admin", dependencies=[Depends(deps.require_admin)])


@_protected.get("/vip")
async def _vip_page() -> dict:
    return {"ok": True}


@_protected.post("/vip")
async def _vip_post() -> dict:
    return {"ok": True}


def _settings(password: str | None = "pw") -> SimpleNamespace:
    return SimpleNamespace(
        admin_user="admin",
        admin_password=SecretStr(password) if password is not None else None,
        session_secret=SecretStr("session-secret"),
    )


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    current = _settings()
    monkeypatch.setattr(deps, "get_settings", lambda: current)
    monkeypatch.setattr(auth, "get_settings", lambda: current)
    monkeypatch.setattr(admin_login, "_FAIL_DELAY", 0)
    return current


@pytest.fixture
def client(settings) -> TestClient:
    app = FastAPI()
    app.include_router(admin_login.router)
    app.include_router(_protected)
    # https: cookie выдаётся с Secure, по http клиент её обратно не пошлёт.
    with TestClient(app, base_url="https://testserver", follow_redirects=False) as c:
        yield c


def _login(client: TestClient, password: str = "pw", next_: str = "/admin/vip"):
    return client.post(
        "/admin/login", data={"username": "admin", "password": password, "next": next_}
    )


def test_bez_vhoda_get_uvodit_na_formu(client: TestClient) -> None:
    response = client.get("/admin/vip?status=paid")
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login?next=%2Fadmin%2Fvip%3Fstatus%3Dpaid"
    # Окно Basic поверх формы браузер открывать не должен.
    assert "www-authenticate" not in response.headers


def test_bez_vhoda_post_401(client: TestClient) -> None:
    response = client.post("/admin/vip")
    assert response.status_code == 401
    assert "www-authenticate" not in response.headers


def test_forma_otkryvaetsya(client: TestClient) -> None:
    response = client.get("/admin/login?next=/admin/vip")
    assert response.status_code == 200
    assert 'name="password"' in response.text
    assert 'value="/admin/vip"' in response.text


def test_vhod_stavit_cookie_i_puskaet(client: TestClient) -> None:
    response = _login(client)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/vip"
    cookie = response.headers["set-cookie"]
    assert deps.ADMIN_COOKIE in cookie
    assert "HttpOnly" in cookie and "Secure" in cookie and "Path=/admin" in cookie

    assert client.get("/admin/vip").status_code == 200
    assert client.post("/admin/vip").status_code == 200


def test_nevernyy_parol_bez_cookie(client: TestClient) -> None:
    response = _login(client, password="nope")
    assert response.status_code == 401
    assert "Неверный логин или пароль" in response.text
    assert "set-cookie" not in response.headers
    assert client.get("/admin/vip").status_code == 303


@pytest.mark.parametrize(
    "raw",
    ["https://evil.example/admin", "//evil.example", "/\\evil.example", "/pay/1", "/admin/login", ""],
)
def test_next_tolko_vnutri_adminki(client: TestClient, raw: str) -> None:
    assert _login(client, next_=raw).headers["location"] == "/admin"


def test_vyhod_snimaet_cookie(client: TestClient) -> None:
    _login(client)
    response = client.post("/admin/logout")
    assert response.status_code == 303
    assert client.get("/admin/vip").status_code == 303


def test_smena_parolya_razloginivaet(client: TestClient, settings) -> None:
    _login(client)
    settings.admin_password = SecretStr("new")
    assert client.get("/admin/vip").status_code == 303


def test_polzovatelskiy_token_ne_vpuskaet(client: TestClient) -> None:
    # Сессия приложения подписана session_secret — за админскую сойти не должна.
    client.cookies.set(deps.ADMIN_COOKIE, auth.issue_session(1, auth.SURFACE_WEB), path="/admin")
    assert client.get("/admin/vip").status_code == 303


def test_basic_ostalsya_zapasnym_vhodom(client: TestClient) -> None:
    assert client.get("/admin/vip", auth=("admin", "pw")).status_code == 200
    wrong = client.get("/admin/vip", auth=("admin", "nope"))
    assert wrong.status_code == 401
    assert wrong.headers["www-authenticate"] == "Basic"


def test_bez_parolya_503(client: TestClient, settings) -> None:
    settings.admin_password = None
    assert client.get("/admin/vip").status_code == 503
    assert client.get("/admin/login").status_code == 503
