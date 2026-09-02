"""Проверки веб-входа через VK ID (app/web/routes/vkid.py).

Базы и сети в тестах нет: обмен кода, запись пользователя и событие
подменены, живой обмен проверен вручную 02.09.2026. Смысл проверок —
отказные ветки: человек нажал «Отмена», cookie попытки протухла, вернулся
чужой state. Все они выглядят одинаково («вход не удался»), и перепутать их
при правке легко, а увидеть на проде — нет.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from app.db import get_session
from app.main import app
from app.models import Platform
from app.vk import id as vk_id
from app.web.routes import vkid

VK_USER_ID = "182907601"
TOKEN = "jwt-zaglushka"


@dataclass
class FakeUser:
    id: int = 1


class FakeSession:
    """Сессия-пустышка: маршруту от неё нужен только commit."""

    def __init__(self) -> None:
        self.committed = False

    async def commit(self) -> None:
        self.committed = True


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Подменить всё, что ходит наружу, и записать вызовы в словарь."""
    recorded: dict = {}

    async def fake_get_or_create(session, **kwargs):
        recorded["user"] = kwargs
        return FakeUser()

    async def fake_log(session, name, **props):
        recorded["event"] = {"name": name, **props}

    async def fake_exchange(code, device_id, verifier):
        recorded["exchange"] = {"code": code, "device_id": device_id}
        return VK_USER_ID

    monkeypatch.setattr(vkid.users_service, "get_or_create", fake_get_or_create)
    monkeypatch.setattr(vkid.events, "log", fake_log)
    monkeypatch.setattr(vkid.vk_id, "exchange_code", fake_exchange)
    monkeypatch.setattr(vkid.auth, "issue_session", lambda user_id: TOKEN)

    session = FakeSession()

    async def fake_session():
        yield session

    app.dependency_overrides[get_session] = fake_session
    recorded["session"] = session
    yield recorded
    app.dependency_overrides.clear()


@pytest.fixture
def client() -> TestClient:
    # Контекст нужен для lifespan: без него не поднимется пул и клиент LLM.
    with TestClient(app) as test_client:
        yield test_client


def start_and_read_state(client: TestClient) -> str:
    """Пройти первый шаг входа и вернуть state этой попытки."""
    response = client.get("/auth/vkid/start", follow_redirects=False)
    assert response.status_code == 303
    verifier, _, state = client.cookies[vkid.STATE_COOKIE].partition(".")
    assert verifier and state
    return state


# --- Уход на VK -----------------------------------------------------------


def test_start_uvodit_na_vk_i_stavit_cookie(client: TestClient) -> None:
    response = client.get("/auth/vkid/start", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith(vk_id.AUTHORIZE_URL)
    # Секреты попытки уехали в cookie, а не в адрес: challenge в ссылке —
    # это хеш, обменять по нему код нельзя.
    assert vkid.STATE_COOKIE in response.cookies
    assert "code_challenge_method=S256" in response.headers["location"]


def test_start_bez_nastroek_vedet_na_ekran_oshibki(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Пустой VK_ID_CLIENT_ID — не 500 и не 503, а внятный экран."""
    settings = vkid.get_settings()
    monkeypatch.setattr(type(settings), "vk_id_enabled", property(lambda self: False))

    response = client.get("/auth/vkid/start", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == vkid._fail_url()


# --- Возврат: успех -------------------------------------------------------


def test_vozvrat_vpuskaet_v_prilozhenie(client: TestClient, calls: dict) -> None:
    state = start_and_read_state(client)

    response = client.get(
        f"/auth/vkid?code=kod&device_id=ustroystvo&state={state}",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/app/?s={TOKEN}"
    assert calls["exchange"] == {"code": "kod", "device_id": "ustroystvo"}
    assert calls["session"].committed


def test_vozvrat_vedet_v_tu_zhe_stroku_chto_mini_app(client: TestClient, calls: dict) -> None:
    """Главное свойство схемы: платформа VK, а не отдельная веб-личность."""
    state = start_and_read_state(client)

    client.get(
        f"/auth/vkid?code=kod&device_id=ustroystvo&state={state}",
        follow_redirects=False,
    )

    assert calls["user"]["platform"] is Platform.VK
    assert calls["user"]["external_id"] == VK_USER_ID
    assert calls["user"]["source"] == vk_id.SOURCE_VK_ID
    # Браузерный заход отличается от запуска Mini App только здесь.
    assert calls["event"]["platform"] == "web"


def test_cookie_popytki_stiraetsya(client: TestClient, calls: dict) -> None:
    """Код одноразовый: вторая попытка обменять его не должна пройти."""
    state = start_and_read_state(client)

    response = client.get(
        f"/auth/vkid?code=kod&device_id=ustroystvo&state={state}",
        follow_redirects=False,
    )

    assert 'astro_vkid=""' in response.headers["set-cookie"]


# --- Возврат: отказы ------------------------------------------------------


def test_vozvrat_bez_cookie(client: TestClient, calls: dict) -> None:
    """Человек шёл дольше десяти минут или чистил cookie."""
    response = client.get(
        "/auth/vkid?code=kod&device_id=ustroystvo&state=chuzhoy",
        follow_redirects=False,
    )

    assert response.headers["location"] == vkid._fail_url()
    assert "user" not in calls


def test_vozvrat_s_chuzhim_state(client: TestClient, calls: dict) -> None:
    """Ссылку возврата подсунули со стороны — вход не открываем."""
    start_and_read_state(client)

    response = client.get(
        "/auth/vkid?code=kod&device_id=ustroystvo&state=podmena",
        follow_redirects=False,
    )

    assert response.headers["location"] == vkid._fail_url()
    assert "exchange" not in calls


def test_otkaz_v_okne_vk(client: TestClient, calls: dict) -> None:
    """Нажал «Отмена» — VK возвращает без кода."""
    state = start_and_read_state(client)

    response = client.get(f"/auth/vkid?state={state}", follow_redirects=False)

    assert response.headers["location"] == vkid._fail_url()
    assert "exchange" not in calls


def test_vk_otvetil_oshibkoy(
    client: TestClient, calls: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Код протух или VK недоступен — человек видит экран входа, а не 500."""
    state = start_and_read_state(client)

    async def fail(code, device_id, verifier):
        raise vk_id.VkIdError("VK ID ответил 400")

    monkeypatch.setattr(vkid.vk_id, "exchange_code", fail)

    response = client.get(
        f"/auth/vkid?code=kod&device_id=ustroystvo&state={state}",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == vkid._fail_url()
    assert "user" not in calls
