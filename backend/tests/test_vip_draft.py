"""Текст VIP-разбора: нарезка на сообщения, черновик и вариант, доставка в личку."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.llm import client as llm
from app.models import Chart, Order, OrderStatus, ReportType, User
from app.services import vip, vip_draft


def _item(**order_kw) -> vip.VipOrder:
    base = {
        "id": 12, "user_id": 1, "chart_id": 7, "pair_id": None, "token": "tok",
        "report_type": ReportType.VIP_NATAL, "amount": 45_000,
        "status": OrderStatus.PAID, "paid_at": datetime(2026, 10, 8, 18, 0, tzinfo=UTC),
    }
    chart = Chart(
        # Местное время рождения хранится без пояса — так в модели Chart.
        birth_local=datetime(1988, 7, 14, 10, 20),  # noqa: DTZ001
        time_known=True, place_name="Судак",
        calc={"natal": {}},
    )
    return vip.VipOrder(order=Order(**{**base, **order_kw}), user=User(external_id="555"), chart=chart)


def _patch_order(monkeypatch: pytest.MonkeyPatch, item: vip.VipOrder) -> None:
    async def fake_get_order(session, order_id):
        return item

    monkeypatch.setattr(vip, "get_order", fake_get_order)


# --- нарезка -------------------------------------------------------------------


def test_short_text_is_one_message() -> None:
    text = "А\n\nб\n\n\nВ\n\nг"
    assert vip_draft.split_message(text) == [text]


def test_blocks_are_packed_whole() -> None:
    blocks = [f"Блок {i}\n\n" + "слово " * 30 for i in range(5)]
    parts = vip_draft.split_message("\n\n\n".join(b.strip() for b in blocks), limit=500)
    assert all(len(p) <= 500 for p in parts)
    # Блок не рвётся между сообщениями, если влезает целиком.
    assert all(p.startswith("Блок") for p in parts)


def test_long_block_keeps_title_and_loses_nothing() -> None:
    text = "Заголовок\n\nабзац " + "слово " * 900 + "\n\nвторой абзац\n\n\nБлок 2\n\nкороткий"
    parts = vip_draft.split_message(text, limit=1000)
    assert all(len(p) <= 1000 for p in parts)
    assert parts[0].startswith("Заголовок\n\nабзац")
    assert " ".join(" ".join(parts).split()) == " ".join(text.split())


def test_part_random_id_depends_on_text() -> None:
    rid = vip_draft._part_random_id
    assert rid(12, 0, "текст") == rid(12, 0, "текст")
    assert rid(12, 0, "текст") != rid(12, 0, "исправленный текст")
    assert rid(12, 0, "текст") != rid(12, 1, "текст")


# --- черновик и вариант ---------------------------------------------------------


def _patch_llm(monkeypatch: pytest.MonkeyPatch, answer: str) -> None:
    async def fake_chat(system, user, **kw):
        return llm.LLMResult(text=answer, model="m", usage=llm.Usage(1, 1, 0))

    monkeypatch.setattr(vip_draft.llm, "chat", fake_chat)
    monkeypatch.setattr(vip_draft.vip_natal, "build_user", lambda *a, **kw: "user")


_ANSWER = json.dumps({"sections": [{"title": "Главное", "body": "Абзац."}]}, ensure_ascii=False)


async def test_first_draft_goes_to_text_second_to_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    item = _item(status=OrderStatus.PENDING, paid_at=None)
    _patch_order(monkeypatch, item)
    _patch_llm(monkeypatch, _ANSWER)

    await vip_draft.make_draft(None, 12)
    assert item.order.draft_text == "Главное\n\nАбзац." and item.order.draft_candidate is None

    item.order.draft_text = "вычитанный текст"
    await vip_draft.make_draft(None, 12)
    assert item.order.draft_text == "вычитанный текст"
    assert item.order.draft_candidate == "Главное\n\nАбзац."


async def test_broken_answer_is_error(monkeypatch: pytest.MonkeyPatch) -> None:
    item = _item()
    _patch_order(monkeypatch, item)
    _patch_llm(monkeypatch, '{"sections": [{"title": "обрыв')
    with pytest.raises(vip_draft.VipDraftError):
        await vip_draft.make_draft(None, 12)
    assert item.order.draft_text is None


async def test_unchanged_edit_is_error(monkeypatch: pytest.MonkeyPatch) -> None:
    item = _item(draft_text="Главное\n\nАбзац.\n")
    _patch_order(monkeypatch, item)
    _patch_llm(monkeypatch, _ANSWER)
    monkeypatch.setattr(vip_draft.vip_edit, "build_user", lambda *a, **kw: "user")

    with pytest.raises(vip_draft.VipDraftError, match="ничего не изменил"):
        await vip_draft.request_edit(None, 12, "сделай короче вдвое")
    assert item.order.draft_candidate is None


async def test_accept_reject_and_manual_save(monkeypatch: pytest.MonkeyPatch) -> None:
    item = _item(draft_text="старый", draft_candidate="новый")
    _patch_order(monkeypatch, item)

    await vip_draft.accept_candidate(None, 12)
    assert (item.order.draft_text, item.order.draft_candidate) == ("новый", None)

    item.order.draft_candidate = "ещё вариант"
    await vip_draft.reject_candidate(None, 12)
    assert (item.order.draft_text, item.order.draft_candidate) == ("новый", None)

    await vip_draft.save_draft(None, 12, "  А\r\n\r\nб  ")
    assert item.order.draft_text == "А\n\nб"
    with pytest.raises(vip_draft.VipDraftError):
        await vip_draft.save_draft(None, 12, " \r\n ")


async def test_delivered_text_is_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_order(monkeypatch, _item(draft_text="текст", delivered_at=datetime.now(UTC)))
    with pytest.raises(vip_draft.VipDraftError):
        await vip_draft.save_draft(None, 12, "правка")


# --- доставка --------------------------------------------------------------------


def _patch_send(monkeypatch: pytest.MonkeyPatch, fail_on: int | None = None) -> list[str]:
    sent: list[str] = []

    async def fake_send(user_ids, text, *, random_id, keyboard=None):
        error = 901 if len(sent) == fail_on else None
        sent.append(text)
        return [vip.vk_api.SendResult(user_id=user_ids[0], message_id=None if error else 1, error_code=error)]

    monkeypatch.setattr(vip_draft, "get_settings", lambda: SimpleNamespace(vk_messaging_enabled=True))
    monkeypatch.setattr(vip_draft.vk_api, "send_messages", fake_send)
    return sent


@pytest.mark.parametrize(
    "order_kw",
    [
        {"status": OrderStatus.PENDING, "paid_at": None, "draft_text": "текст"},
        {"draft_text": "текст", "draft_candidate": "вариант"},
        {"draft_text": None},
    ],
)
async def test_deliver_refuses(monkeypatch: pytest.MonkeyPatch, order_kw: dict) -> None:
    _patch_order(monkeypatch, _item(**order_kw))
    sent = _patch_send(monkeypatch)
    with pytest.raises(vip_draft.VipDraftError):
        await vip_draft.deliver(None, 12)
    assert sent == []


async def test_deliver_sends_header_and_marks_delivered(monkeypatch: pytest.MonkeyPatch) -> None:
    item = _item(draft_text="Главное\n\nАбзац.")
    _patch_order(monkeypatch, item)
    sent = _patch_send(monkeypatch)

    await vip_draft.deliver(None, 12)
    assert sent == ["Твоя натальная карта: 14.07.1988, 10:20, Судак\n\n\nГлавное\n\nАбзац."]
    assert item.order.delivered_at is not None
    assert item.order.draft_text == sent[0]


async def test_deliver_broken_midway_is_not_delivered(monkeypatch: pytest.MonkeyPatch) -> None:
    item = _item(draft_text="\n\n\n".join(f"Блок {i}\n\n" + "слово " * 600 for i in range(3)))
    _patch_order(monkeypatch, item)
    sent = _patch_send(monkeypatch, fail_on=1)

    with pytest.raises(vip_draft.VipDraftError, match="Ушло 1 из"):
        await vip_draft.deliver(None, 12)
    assert len(sent) == 2
    assert item.order.delivered_at is None
