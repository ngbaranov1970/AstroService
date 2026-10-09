"""VIP-заказ: срок, ссылка на оплату, разбор VK id и отказ в генерации."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.models import Chart, Order, OrderStatus, ReportType, User
from app.services import admin as admin_service
from app.services import vip
from app.web.routes import pay


def _order(**kw) -> Order:
    base = {
        "id": 1, "user_id": 1, "chart_id": 7, "pair_id": None, "token": "tok",
        "report_type": ReportType.VIP_NATAL, "amount": 45_000, "status": OrderStatus.PENDING,
    }
    return Order(**{**base, **kw})


def test_due_at_is_day_after_payment() -> None:
    paid_at = datetime(2026, 10, 8, 18, 0, tzinfo=UTC)
    item = vip.VipOrder(order=_order(status=OrderStatus.PAID, paid_at=paid_at), user=User(), chart=None)
    assert item.due_at == datetime(2026, 10, 9, 18, 0, tzinfo=UTC)
    assert item.in_work


def test_unpaid_and_delivered_are_not_in_work() -> None:
    pending = vip.VipOrder(order=_order(), user=User(), chart=None)
    assert pending.due_at is None and not pending.in_work
    delivered = _order(status=OrderStatus.PAID, paid_at=datetime.now(UTC), delivered_at=datetime.now(UTC))
    assert not vip.VipOrder(order=delivered, user=User(), chart=None).in_work


def test_payment_url(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = SimpleNamespace(payments_enabled=True, public_base_url="https://astrozvezdochet.ru/")
    monkeypatch.setattr(vip, "get_settings", lambda: settings)
    assert vip.payment_url(_order()) == "https://astrozvezdochet.ru/pay/order/tok"

    settings.payments_enabled = False
    assert vip.payment_url(_order()) is None


@pytest.mark.parametrize(("query", "expected"), [("123", 123), ("id123", 123), ("https://vk.com/id5", 5)])
async def test_resolve_plain_vk_id_without_api(
    monkeypatch: pytest.MonkeyPatch, query: str, expected: int
) -> None:
    async def must_not_call(screen):
        raise AssertionError("числовой id в VK не резолвим")

    monkeypatch.setattr(admin_service.vk_api, "resolve_screen_name", must_not_call)
    assert await admin_service.resolve_vk_user_id(query) == expected


async def test_resolve_name_is_not_vk(monkeypatch: pytest.MonkeyPatch) -> None:
    async def must_not_call(screen):
        raise AssertionError("имя человека — не короткое имя VK")

    monkeypatch.setattr(admin_service.vk_api, "resolve_screen_name", must_not_call)
    assert await admin_service.resolve_vk_user_id("Вася Пупкин") is None


def test_chart_return_page_rejects_vip_order() -> None:
    # Возврат по токену карты поставил бы VIP в генерацию — промпта у него нет.
    target = pay._Target(chart=Chart(id=7), pair=None, owner=User(id=1), rtype=ReportType.FULL_NATAL)
    assert not pay._order_matches(_order(), target)
    assert pay._order_matches(_order(report_type=ReportType.FULL_NATAL), target)


# --- Уведомление об оплате ---------------------------------------------------

_PAID_AT = datetime(2026, 10, 8, 18, 0, tzinfo=UTC)


def _paid_item() -> vip.VipOrder:
    user = User(external_id="555", platform_username="Анна Звёздная")
    return vip.VipOrder(order=_order(id=12, status=OrderStatus.PAID, paid_at=_PAID_AT), user=user, chart=None)


def _settings(**kw) -> SimpleNamespace:
    base = {
        "vk_messaging_enabled": True, "vk_group_id": 777, "vip_owner_vk_id": 42,
        "public_base_url": "https://astrozvezdochet.ru",
    }
    return SimpleNamespace(**{**base, **kw})


def test_owner_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vip, "get_settings", lambda: _settings())
    text = vip.owner_paid_text(_paid_item(), client_note="Клиенту НЕ ушло")
    assert "№12: Анна Звёздная, 450 ₽" in text
    assert "09.10 21:00 МСК" in text
    assert "https://vk.ru/gim777?sel=555" in text
    assert "Клиенту НЕ ушло" in text
    assert text.endswith("https://astrozvezdochet.ru/admin/vip/12")


class _NoSession:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return False


def _patch_notify(monkeypatch: pytest.MonkeyPatch, settings, client_error: int | None) -> list:
    sent: list = []

    async def fake_get_order(session, order_id):
        return _paid_item()

    async def fake_send(user_ids, text, *, random_id, keyboard=None):
        sent.append((user_ids, text, random_id))
        error = client_error if user_ids == [555] else None
        return [vip.vk_api.SendResult(user_id=user_ids[0], message_id=None if error else 1, error_code=error)]

    monkeypatch.setattr(vip, "get_settings", lambda: settings)
    monkeypatch.setattr(vip, "get_sessionmaker", lambda: _NoSession)
    monkeypatch.setattr(vip, "get_order", fake_get_order)
    monkeypatch.setattr(vip.vk_api, "send_messages", fake_send)
    return sent


async def test_notify_tells_owner_that_client_letter_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _patch_notify(monkeypatch, _settings(), client_error=901)
    await vip.notify_paid(12)
    assert [ids for ids, _, _ in sent] == [[555], [42]]
    assert "Клиенту НЕ ушло (VK 901)" in sent[1][1]
    # random_id у двух писем разный, иначе VK счёл бы второе повтором.
    assert sent[0][2] != sent[1][2]


async def test_notify_without_owner_id_writes_only_client(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _patch_notify(monkeypatch, _settings(vip_owner_vk_id=None), client_error=None)
    await vip.notify_paid(12)
    assert [ids for ids, _, _ in sent] == [[555]]


async def test_notify_is_stable_per_order() -> None:
    assert vip._random_id(12, "client") == vip._random_id(12, "client")
    assert vip._random_id(12, "client") != vip._random_id(13, "client")
