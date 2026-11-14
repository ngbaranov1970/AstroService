"""Маршруты карточки VIP-заказа (app/web/routes/admin.py, /admin/vip/{id}).

Базы и сети нет: охрана, сессия, выборка заказа, DeepSeek и VK подменены, а
функции vip_draft — настоящие. Смысл — успех коммитит и уводит редиректом,
VipDraftError откатывает и показывает ошибку в карточке, а карточка в каждом
состоянии заказа даёт только допустимые в нём кнопки.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.db import get_session
from app.llm import client as llm
from app.main import app
from app.models import Chart, Order, OrderStatus, ReportType, User
from app.services import vip, vip_check, vip_draft
from app.web.deps import require_admin
from app.web.routes import pay
from astro_core.natal import compute_natal

URL = "/admin/vip/12"


class FakeSession:
    """Сессия-пустышка: маршруту от неё нужны только commit и rollback."""

    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


def _item(**order_kw) -> vip.VipOrder:
    base = {
        "id": 12, "user_id": 1, "chart_id": 7, "pair_id": None, "token": "tok",
        "report_type": ReportType.VIP_NATAL, "amount": 45_000,
        "status": OrderStatus.PAID, "paid_at": datetime(2026, 10, 8, 18, 0, tzinfo=UTC),
        "created_at": datetime(2026, 10, 8, 17, 0, tzinfo=UTC),
    }
    chart = Chart(
        birth_local=datetime(1988, 7, 14, 10, 20),  # noqa: DTZ001
        time_known=True, place_name="Судак", tz="Europe/Simferopol", token="ct",
        calc={"natal": {}},
    )
    user = User(id=1, external_id="555", platform_username="Анна Звёздная")
    return vip.VipOrder(order=Order(**{**base, **order_kw}), user=user, chart=chart)


@pytest.fixture
def session() -> FakeSession:
    return FakeSession()


@pytest.fixture
def order(monkeypatch: pytest.MonkeyPatch, session: FakeSession) -> dict:
    """Подменить охрану, сессию и выборку; заказ лежит в словаре, тест кладёт свой."""
    holder = {"item": _item()}

    async def fake_get_order(s, order_id):
        return holder["item"] if order_id == 12 else None

    async def fake_session():
        yield session

    # Находка — начало текста: по ней видно, какой текст проверка получила.
    def fake_check(text, natal, *, time_known=True):
        return [vip_check.Finding("house", text[:12], "проверь дом", 0, 12)]

    monkeypatch.setattr(vip, "get_order", fake_get_order)
    monkeypatch.setattr(vip_check, "check", fake_check)
    app.dependency_overrides[get_session] = fake_session
    app.dependency_overrides[require_admin] = lambda: "admin"
    yield holder
    app.dependency_overrides.clear()


@pytest.fixture
def client() -> TestClient:
    with TestClient(app, follow_redirects=False) as test_client:
        yield test_client


# --- действия ----------------------------------------------------------------


def test_unknown_action_is_404(client: TestClient, order: dict, session: FakeSession) -> None:
    response = client.post(f"{URL}/drop", data={"text": "x"})
    assert response.status_code == 404
    assert session.commits == 0


def test_save_commits_and_redirects(client: TestClient, order: dict, session: FakeSession) -> None:
    order["item"] = _item(draft_text="старый")
    response = client.post(f"{URL}/save", data={"text": "Новый\r\n\r\nтекст"})

    assert response.status_code == 303
    assert response.headers["location"] == URL
    assert order["item"].order.draft_text == "Новый\n\nтекст"
    assert (session.commits, session.rollbacks) == (1, 0)


def test_error_rolls_back_and_shows_on_card(
    client: TestClient, order: dict, session: FakeSession
) -> None:
    order["item"] = _item(draft_text="текст")
    response = client.post(f"{URL}/accept")

    assert response.status_code == 200
    assert "Нового варианта нет" in response.text
    assert (session.commits, session.rollbacks) == (0, 1)


def test_failed_edit_keeps_request(
    client: TestClient, order: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken_chat(system, user, **kw):
        return llm.LLMResult(text='{"sections": [{"title": "обрыв', model="m", usage=llm.Usage(1, 1, 0))

    monkeypatch.setattr(vip_draft.llm, "chat", broken_chat)
    monkeypatch.setattr(vip_draft.vip_edit, "build_user", lambda *a, **kw: "user")
    order["item"] = _item(draft_text="текст")
    response = client.post(f"{URL}/edit", data={"text": "убери повтор про Луну"})

    assert response.status_code == 200
    assert "не разобрался" in response.text
    # Просьба вернулась в поле: набирать её заново после сбоя модели не нужно.
    assert "убери повтор про Луну</textarea>" in response.text
    assert order["item"].order.draft_candidate is None


def test_unpaid_deliver_is_refused(
    client: TestClient, order: dict, session: FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def must_not_send(*a, **kw):
        raise AssertionError("неоплаченный заказ в личку не уходит")

    monkeypatch.setattr(vip_draft.vk_api, "send_messages", must_not_send)
    order["item"] = _item(status=OrderStatus.PENDING, paid_at=None, draft_text="текст")
    response = client.post(f"{URL}/deliver")

    assert response.status_code == 200
    assert "не оплачен" in response.text
    assert session.commits == 0


def test_draft_is_generated_in_request(
    client: TestClient, order: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    answer = json.dumps({"sections": [{"title": "Главное", "body": "Абзац."}]}, ensure_ascii=False)

    async def fake_chat(system, user, **kw):
        return llm.LLMResult(text=answer, model="m", usage=llm.Usage(1, 1, 0))

    monkeypatch.setattr(vip_draft.llm, "chat", fake_chat)
    monkeypatch.setattr(vip_draft.vip_natal, "build_user", lambda *a, **kw: "user")
    response = client.post(f"{URL}/draft")

    assert response.status_code == 303
    assert order["item"].order.draft_text == "Главное\n\nАбзац."


def test_draft_uses_chosen_model(
    client: TestClient, order: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict = {}

    async def fake_chat(system, user, **kw):
        seen.update(kw)
        return llm.LLMResult(text='{"sections": [{"title": "Т", "body": "А."}]}', model="m", usage=llm.Usage(1, 1, 0))

    monkeypatch.setattr(vip_draft.llm, "chat", fake_chat)
    monkeypatch.setattr(vip_draft.vip_natal, "build_user", lambda *a, **kw: "user")
    response = client.post(f"{URL}/draft", data={"model": "deepseek-v4-pro"})

    assert response.status_code == 303
    assert seen["model"] == "deepseek-v4-pro"


def test_draft_refuses_unknown_model(
    client: TestClient, order: dict, session: FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def must_not_call(*a, **kw):
        raise AssertionError("модель не из списка не вызывается")

    monkeypatch.setattr(vip_draft.llm, "chat", must_not_call)
    response = client.post(f"{URL}/draft", data={"model": "openai/gpt-6-astra-pro"})

    assert response.status_code == 200
    assert "нет в списке" in response.text
    assert session.commits == 0


# --- карточка в разных состояниях -------------------------------------------------


@pytest.mark.parametrize(
    ("order_kw", "present", "absent"),
    [
        pytest.param(
            {"status": OrderStatus.PENDING, "paid_at": None},
            ["Сделать черновик", "/pay/order/tok", "<option value=\"anthropic/claude-opus-5.5\" selected>Claude Opus"],
            ["Сохранить", "отправить в личку"],
            id="ждёт оплаты, черновика нет",
        ),
        pytest.param(
            {"status": OrderStatus.PENDING, "paid_at": None, "draft_text": "Черновик до оплаты"},
            ["Сохранить", "Попросить модель", "после оплаты", "«Черновик до »"],
            ["Готово — отправить в личку"],
            id="черновик до оплаты",
        ),
        pytest.param(
            {"draft_text": "Вычитанный текст"},
            ["Сохранить", "Готово — отправить в личку", "«Вычитанный т»"],
            ["Сделать черновик", "Принять вариант"],
            id="оплачен, черновик",
        ),
        pytest.param(
            {"draft_text": "Вычитанный текст", "draft_candidate": "Вариант модели"},
            ["Новый вариант", "Принять вариант", "Отказаться", "«Вычитанный т»", "«Вариант моде»"],
            ['name="text" rows="30"', "Готово — отправить в личку"],
            id="есть непринятый вариант",
        ),
        pytest.param(
            {"draft_text": "Отправленный текст", "delivered_at": datetime(2026, 10, 9, 9, 0, tzinfo=UTC)},
            ["Отправлено в личку", "Отправленный текст"],
            ["Сохранить", "Попросить модель", "/draft", "/deliver"],
            id="отправлен",
        ),
    ],
)
def test_card_shows_only_allowed_buttons(
    client: TestClient,
    order: dict,
    monkeypatch: pytest.MonkeyPatch,
    order_kw: dict,
    present: list[str],
    absent: list[str],
) -> None:
    # Ссылка на оплату не зависит от .env разработчика.
    monkeypatch.setattr(vip, "payment_url", lambda o: f"https://astrozvezdochet.ru/pay/order/{o.token}")
    order["item"] = _item(**order_kw)
    response = client.get(URL)

    assert response.status_code == 200
    for text in present:
        assert text in response.text, text
    for text in absent:
        assert text not in response.text, text


def test_missing_order_is_404(client: TestClient, order: dict) -> None:
    assert client.get("/admin/vip/99").status_code == 404


# --- заказ пары (vip_pair) -----------------------------------------------------


def _pair_item(**order_kw) -> vip.VipOrder:
    """Заказ пары: карта клиента и партнёр в pair_data, наталы настоящие."""
    place = (55.3833, 39.0333, "Europe/Moscow")
    birth = datetime(1987, 3, 9, 14, 30)  # noqa: DTZ001
    partner_natal = compute_natal(datetime(1990, 5, 11, 12, 0), *place)  # noqa: DTZ001
    pair_data = {
        "ref": "pair_post", "name": "Маша", "date": "1990-05-11", "time": None,
        "time_known": False, "place_known": False,
        "place": {"label": "Егорьевск, RU", "lat": place[0], "lon": place[1], "tz": place[2]},
        "natal": partner_natal,
    }
    base = {
        "id": 12, "user_id": 1, "chart_id": 7, "pair_id": None, "token": "tok",
        "report_type": ReportType.VIP_PAIR, "amount": 39_000,
        "status": OrderStatus.PAID, "paid_at": datetime(2026, 11, 14, 18, 0, tzinfo=UTC),
        "created_at": datetime(2026, 11, 14, 17, 0, tzinfo=UTC), "pair_data": pair_data,
    }
    chart = Chart(
        id=7, birth_local=birth, time_known=True, place_name="Егорьевск, RU",
        tz="Europe/Moscow", token="ct", calc={"natal": compute_natal(birth, *place)},
    )
    user = User(id=1, external_id="555", platform_username="Анна Звёздная")
    return vip.VipOrder(order=Order(**{**base, **order_kw}), user=user, chart=chart)


def test_pair_card_shows_both_sides_and_hides_model_edit(client: TestClient, order: dict) -> None:
    order["item"] = _pair_item(draft_text="Его Марс в твоём седьмом доме. Совместимость 90%.")
    response = client.get(URL)

    assert response.status_code == 200
    assert "Партнёр" in response.text and "Маша, 11.05.1990, время неизвестно" in response.text
    assert "pair_post" in response.text
    # Находки — от проверки пары, а не от натальной.
    assert "домов в расчёте пары нет" in response.text
    assert "процентов и оценок совместимости" in response.text
    # Правки просьбой к модели у пары нет; новый черновик и отправка — есть.
    assert "Попросить модель" not in response.text
    assert "Новый черновик с нуля" in response.text
    assert f'action="{URL}/deliver"' in response.text


def test_pair_pay_page_hides_birth_data(
    client: TestClient, order: dict, monkeypatch: pytest.MonkeyPatch, session: FakeSession
) -> None:
    item = _pair_item(status=OrderStatus.PENDING, paid_at=None)

    async def fake_get_by_token(s, token):
        return item.order if token == "tok" else None

    async def fake_get(model, ident):
        return item.chart

    async def fake_user(s, user_id):
        return item.user

    async def no_paid(s, **kw):
        return None

    session.get = fake_get  # type: ignore[attr-defined]
    monkeypatch.setattr(pay.orders, "get_by_token", fake_get_by_token)
    monkeypatch.setattr(pay.users, "get_by_id", fake_user)
    monkeypatch.setattr(pay.vip_pair, "find_paid", no_paid)

    response = client.get("/pay/order/tok")
    assert response.status_code == 200
    assert "Разбор пары с астрологом" in response.text and "390 ₽" in response.text
    assert "Разбор совместимости двух карт" in response.text
    # За парой двое людей: ни даты, ни города на витрине нет.
    assert "09.03.1987" not in response.text and "Егорьевск" not in response.text
    assert 'action="/pay/order/tok"' in response.text
