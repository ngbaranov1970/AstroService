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
