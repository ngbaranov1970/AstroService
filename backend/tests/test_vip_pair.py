"""Заказ пары из бота: партнёр в pair_data, заказ, черновик, проверка фактов, тизер.

Без БД, сети и LLM: выборки заказов и вставка подменены, наталы и синастрия настоящие.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm import client as llm
from app.llm.prompts import pair_teaser as teaser_prompt
from app.llm.prompts import vip_pair as vip_pair_prompt
from app.llm.prompts import vip_pair_edit
from app.models import Chart, ClientGender, Order, OrderStatus, ReportType, User
from app.services import charts, orders, pair_teaser, vip, vip_check, vip_draft, vip_pair
from app.vk import api as vk_api
from astro_core.natal import compute_natal
from astro_core.synastry import compute_synastry

PLACE = charts.Place(label="Егорьевск, RU", lat=55.3833, lon=39.0333, tz="Europe/Moscow")
OTHER_PLACE = charts.Place(label="Самара, RU", lat=53.2, lon=50.15, tz="Europe/Samara")
# Местное время рождения хранится без пояса — так в модели Chart.
BIRTH = datetime(1987, 3, 9, 14, 30)  # noqa: DTZ001
MASHA = vip_pair.Partner(birth_date=date(1990, 5, 11), name="Маша")
LENA = vip_pair.Partner(birth_date=date(1992, 1, 2), birth_time=time(7, 15), place=OTHER_PLACE)

SESSION = cast(AsyncSession, object())


def _chart(chart_id: int = 7) -> Chart:
    natal = compute_natal(BIRTH, PLACE.lat, PLACE.lon, PLACE.tz)
    return Chart(id=chart_id, user_id=1, time_known=True, calc={"natal": natal})


@pytest.fixture
def base(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Подмены базы: «таблица» заказов списком и счётчик вставок."""
    state: dict[str, Any] = {"orders": [], "inserted": 0, "chart": _chart()}

    async def fake_create_chart(session, **kw):
        return state["chart"]

    async def fake_orders_of(session, user_id, status):
        found = [o for o in state["orders"] if o.status is status]
        return sorted(found, key=lambda o: o.id, reverse=True)

    async def fake_insert(session, *, user_id, chart_id, data, amount, gender=None):
        state["inserted"] += 1
        order = Order(
            id=100 + state["inserted"], user_id=user_id, chart_id=chart_id,
            report_type=ReportType.VIP_PAIR, amount=amount,
            status=OrderStatus.PENDING, pair_data=data, client_gender=gender,
        )
        state["orders"].append(order)
        return order

    monkeypatch.setattr(vip_pair.charts, "create_chart", fake_create_chart)
    monkeypatch.setattr(vip_pair, "_orders_of", fake_orders_of)
    monkeypatch.setattr(vip_pair, "_insert", fake_insert)
    return state


async def _create(partner: vip_pair.Partner, ref: str = "pair_dm") -> Order:
    return await vip_pair.create_order(
        SESSION, user=User(id=1), birth_local=BIRTH, birth_time_known=True,
        place=PLACE, partner=partner, ref=ref,
    )


# --- партнёр -------------------------------------------------------------------


async def test_partner_without_time_is_counted_at_noon_in_client_city() -> None:
    data = await vip_pair.partner_data(MASHA, client_place=PLACE, ref="pair_post")
    assert data["time_known"] is False and data["time"] is None
    assert data["place_known"] is False and data["place"]["label"] == PLACE.label
    assert data["natal"]["meta"]["birth_local"] == "1990-05-11T12:00:00"
    assert data["natal"]["meta"]["tz"] == PLACE.tz
    assert data["ref"] == "pair_post" and data["name"] == "Маша"


async def test_partner_with_time_uses_own_city() -> None:
    data = await vip_pair.partner_data(LENA, client_place=PLACE, ref=None)
    assert data["time_known"] is True and data["time"] == "07:15"
    assert data["place_known"] is True and data["natal"]["meta"]["tz"] == OTHER_PLACE.tz


async def test_partner_key_ignores_name() -> None:
    a = await vip_pair.partner_data(MASHA, client_place=PLACE, ref=None)
    renamed = vip_pair.Partner(birth_date=MASHA.birth_date, name="Мария")
    b = await vip_pair.partner_data(renamed, client_place=PLACE, ref=None)
    c = await vip_pair.partner_data(LENA, client_place=PLACE, ref=None)
    assert vip_pair.partner_key(a) == vip_pair.partner_key(b)
    assert vip_pair.partner_key(a) != vip_pair.partner_key(c)


async def test_synastry_drops_axes_of_partner_without_time() -> None:
    data = await vip_pair.partner_data(MASHA, client_place=PLACE, ref=None)
    calc = vip_pair.synastry(_chart(), data)
    assert calc["meta"]["b"]["time_known"] is False
    assert not any(a["b"] in {"Ascendant", "Medium_Coeli"} for a in calc["aspects"])


# --- заказ ---------------------------------------------------------------------


async def test_new_order(base: dict[str, Any]) -> None:
    order = await _create(MASHA)
    assert order.report_type is ReportType.VIP_PAIR
    # Сумму сверяем с прайсом, а не с числом: цена меняется решением владельца.
    assert order.amount == orders.price(ReportType.VIP_PAIR)
    assert order.pair_data["ref"] == "pair_dm" and order.chart_id == 7
    assert base["inserted"] == 1


async def test_repeat_returns_same_pending_order(base: dict[str, Any]) -> None:
    first = await _create(MASHA)
    first.draft_text = "вычитанный текст"
    again = await _create(MASHA, ref="pair_menu")
    assert again is first and base["inserted"] == 1
    # Тот же партнёр — черновик цел, ref обновился.
    assert again.draft_text == "вычитанный текст"
    assert again.pair_data["ref"] == "pair_menu"


async def test_other_partner_rewrites_pending_and_drops_draft(base: dict[str, Any]) -> None:
    first = await _create(MASHA)
    first.draft_text, first.draft_candidate = "про Машу", "вариант про Машу"
    second = await _create(LENA)
    assert second is first and base["inserted"] == 1
    assert second.pair_data["date"] == "1992-01-02"
    assert second.draft_text is None and second.draft_candidate is None


async def test_paid_order_for_same_partner_is_returned(base: dict[str, Any]) -> None:
    first = await _create(MASHA)
    first.status = OrderStatus.PAID
    again = await _create(MASHA)
    assert again is first and base["inserted"] == 1


async def test_other_partner_after_paid_gets_new_order(base: dict[str, Any]) -> None:
    first = await _create(MASHA)
    first.status = OrderStatus.PAID
    first.draft_text = "про Машу"
    second = await _create(LENA)
    assert second is not first and base["inserted"] == 2
    assert second.status is OrderStatus.PENDING
    # Оплаченный заказ не тронут.
    assert first.pair_data["date"] == "1990-05-11" and first.draft_text == "про Машу"


async def test_paid_is_checked_by_partner_not_by_client_chart(base: dict[str, Any]) -> None:
    first = await _create(MASHA)
    first.status = OrderStatus.PAID
    lena = await vip_pair.partner_data(LENA, client_place=PLACE, ref=None)
    assert await vip_pair.find_paid(SESSION, user_id=1, chart_id=7, pair_data=lena) is None
    assert await vip_pair.find_paid(
        SESSION, user_id=1, chart_id=7, pair_data=first.pair_data
    ) is first


async def test_gender_is_saved_and_survives_repeat_and_reopen(base: dict[str, Any]) -> None:
    kw = {"user": User(id=1), "birth_local": BIRTH, "birth_time_known": True, "place": PLACE,
          "partner": MASHA, "ref": "pair_dm"}
    order = await vip_pair.create_order(SESSION, **kw, client_gender=ClientGender.FEMALE)
    assert order.client_gender is ClientGender.FEMALE
    # Повтор исправляет ошибочное нажатие; повтор без ответа род не стирает.
    assert (await vip_pair.create_order(SESSION, **kw, client_gender=ClientGender.MALE)) is order
    assert order.client_gender is ClientGender.MALE
    await vip_pair.create_order(SESSION, **kw)
    assert order.client_gender is ClientGender.MALE
    # Новый заказ после мёртвого платежа род наследует.
    order.status = OrderStatus.CANCELED
    assert (await vip_pair.reopen(SESSION, order)).client_gender is ClientGender.MALE


async def test_reopen_returns_live_order_as_is(base: dict[str, Any]) -> None:
    order = await _create(MASHA)
    assert await vip_pair.reopen(SESSION, order) is order
    assert base["inserted"] == 1


async def test_reopen_after_dead_payment_keeps_partner_and_draft(base: dict[str, Any]) -> None:
    dead = await _create(MASHA)
    dead.status = OrderStatus.CANCELED
    dead.draft_text = "черновик"
    live = await vip_pair.reopen(SESSION, dead)
    assert live is not dead and live.status is OrderStatus.PENDING
    assert live.pair_data == dead.pair_data and live.draft_text == "черновик"
    # Повторный заход по старой ссылке второго заказа не заводит.
    assert await vip_pair.reopen(SESSION, dead) is live and base["inserted"] == 2


async def test_reopen_finds_paid_order_for_same_partner(base: dict[str, Any]) -> None:
    dead = await _create(MASHA)
    dead.status = OrderStatus.CANCELED
    paid = await vip_pair.reopen(SESSION, dead)
    paid.status = OrderStatus.PAID
    assert await vip_pair.reopen(SESSION, dead) is paid


# --- черновик, проверка фактов и доставка (services/vip_draft, vip_check) --------


async def _pair_item(**order_kw) -> vip.VipOrder:
    data = await vip_pair.partner_data(MASHA, client_place=PLACE, ref="pair_post")
    base = {
        "id": 12, "user_id": 1, "chart_id": 7, "token": "tok", "amount": 39_000,
        "report_type": ReportType.VIP_PAIR, "status": OrderStatus.PAID,
        "paid_at": datetime(2026, 11, 14, 18, 0, tzinfo=UTC), "pair_data": data,
    }
    chart = _chart()
    chart.birth_local, chart.place_name = BIRTH, "Егорьевск, RU"
    return vip.VipOrder(order=Order(**{**base, **order_kw}), user=User(external_id="555"), chart=chart)


def _patch_get_order(monkeypatch: pytest.MonkeyPatch, item: vip.VipOrder) -> None:
    async def fake_get_order(session, order_id):
        return item

    monkeypatch.setattr(vip, "get_order", fake_get_order)


class _Session:
    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)


async def test_draft_for_pair_uses_pair_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    item = await _pair_item()
    _patch_get_order(monkeypatch, item)
    seen: dict[str, str] = {}

    async def fake_chat(system, user, **kw):
        seen["system"], seen["user"] = system, user
        body = json.dumps({"sections": [{"title": "Главное про вас двоих", "body": "Текст."}]})
        return SimpleNamespace(text=body, usage=SimpleNamespace(completion_tokens=10))

    monkeypatch.setattr(llm, "chat", fake_chat)
    await vip_draft.make_draft(cast(AsyncSession, _Session()), 12)
    assert seen["system"] == vip_pair_prompt.SYSTEM
    assert "Сторона B: Маша" in seen["user"]
    # Дома клиента, Луна партнёра без времени и транзиты доехали до модели.
    assert "Луна стороны B:" in seen["user"]
    assert "# Планеты B в домах A" in seen["user"] and "К карте B:" in seen["user"]
    assert item.order.draft_text == "Главное про вас двоих\n\nТекст."


async def test_edit_by_model_for_pair_uses_pair_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    item = await _pair_item(draft_text="Главное\n\nСтарый текст.")
    _patch_get_order(monkeypatch, item)
    seen: dict[str, str] = {}

    async def fake_chat(system, user, **kw):
        seen["system"], seen["user"] = system, user
        body = json.dumps({"sections": [{"title": "Главное", "body": "Короче."}]})
        return SimpleNamespace(text=body, usage=SimpleNamespace(completion_tokens=10))

    monkeypatch.setattr(llm, "chat", fake_chat)
    await vip_draft.request_edit(cast(AsyncSession, _Session()), 12, "сделай короче")
    assert seen["system"] == vip_pair_edit.SYSTEM
    # Модель видит данные пары, текущий текст и просьбу.
    assert "Сторона B: Маша" in seen["user"] and "К карте B:" in seen["user"]
    assert "<разбор>\nГлавное\n\nСтарый текст.\n</разбор>" in seen["user"]
    assert "<просьба>\nсделай короче\n</просьба>" in seen["user"]
    # Вычитанный черновик не затёрт: правка ждёт «принять / отказаться».
    assert item.order.draft_text == "Главное\n\nСтарый текст."
    assert item.order.draft_candidate == "Главное\n\nКороче."


async def test_deliver_unpaid_pair_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    item = await _pair_item(status=OrderStatus.PENDING, paid_at=None, draft_text="черновик")
    _patch_get_order(monkeypatch, item)
    session = _Session()
    with pytest.raises(vip_draft.VipDraftError, match="не оплачен"):
        await vip_draft.deliver(cast(AsyncSession, session), 12)
    assert item.order.delivered_at is None and not session.added


async def test_deliver_pair_logs_event_with_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    item = await _pair_item(draft_text="Главное про вас двоих\n\nТекст.")
    _patch_get_order(monkeypatch, item)
    sent: list[str] = []

    async def fake_send(peers, text, *, random_id):
        sent.append(text)
        return [vk_api.SendResult(user_id=peers[0], message_id=1)]

    monkeypatch.setattr(vip_draft.vk_api, "send_messages", fake_send)
    monkeypatch.setattr(
        vip_draft, "get_settings", lambda: SimpleNamespace(vk_messaging_enabled=True)
    )
    session = _Session()
    await vip_draft.deliver(cast(AsyncSession, session), 12)

    assert item.order.delivered_at is not None
    assert sent[0].startswith(
        "Разбор вашей пары\nТы: 09.03.1987, 14:30, Егорьевск, RU\n"
        "Партнёр: Маша, 11.05.1990, время неизвестно"
    )
    [event] = session.added
    assert event.name == "pair_delivered"
    assert event.props == {"order_id": 12, "ref": "pair_post"}


async def test_check_pair_findings() -> None:
    item = await _pair_item()
    pair_data = item.order.pair_data
    calc = vip_pair.synastry(item.chart, pair_data)
    natal_a, natal_b = item.chart.calc["natal"], pair_data["natal"]
    today = date(2026, 11, 16)

    def kinds(text: str, *, a: bool = True, b: bool = False) -> list[str]:
        extras = vip_pair.extras_of(natal_a, natal_b, a_time_known=a, b_time_known=b, today=today)
        found = vip_check.check_pair(
            text, calc, natal_a, natal_b, extras, a_time_known=a, b_time_known=b
        )
        return [f.kind for f in found]

    # Солнце A — соединение — Марс B есть в расчёте: в любую сторону верно.
    assert kinds("Твоё Солнце в соединении с Марсом Маши.") == []
    assert kinds("Марс Маши в соединении с твоим Солнцем.") == []
    # Тот же контакт другого типа.
    assert kinds("Твоё Солнце в квадрате к Марсу Маши.") == ["aspect"]
    # Знак верен, если так стоит хотя бы у одной стороны.
    assert kinds("Солнце в Рыбах, а у Маши Солнце в Тельце.") == []
    assert kinds("Солнце в Скорпионе тут ни при чём.") == ["sign"]
    assert kinds("Совместимость 87% — это много.") == ["percent"]
    # Асцендент и дома — находка, только если времени нет у обоих.
    assert kinds("Твой Асцендент здесь молчит.") == []
    assert kinds("Твой Асцендент здесь молчит.", a=False) == ["no_time"]
    assert kinds("Его Марс в твоём седьмом доме.", a=False) == ["no_time"]
    # Луна стороны без времени приблизительна — её знак не сверяем.
    assert kinds("Луна в Весах делает её мягче.") == []


async def test_check_pair_houses_and_natal_aspects() -> None:
    item = await _pair_item()
    pair_data = item.order.pair_data
    calc = vip_pair.synastry(item.chart, pair_data)
    natal_a, natal_b = item.chart.calc["natal"], pair_data["natal"]
    extras = vip_pair.extras(item.chart, pair_data, date(2026, 11, 16))

    def kinds(text: str) -> list[str]:
        found = vip_check.check_pair(
            text, calc, natal_a, natal_b, extras, a_time_known=True, b_time_known=False
        )
        return [f.kind for f in found]

    own = {p["name_ru"]: p["house"] for p in natal_a["planets"]}
    guest = {p["name_ru"]: p["house"] for p in extras["a"]["overlay"]}
    ordinals = ["", "первом", "втором", "третьем", "четвёртом", "пятом", "шестом", "седьмом",
                "восьмом", "девятом", "десятом", "одиннадцатом", "двенадцатом"]
    # Марс верен и в своём доме, и в доме по наложению; в остальных — находка.
    assert kinds(f"Твой Марс в {ordinals[own['Марс']]} доме.") == []
    assert kinds(f"Марс Маши в {ordinals[guest['Марс']]} доме твоей карты.") == []
    wrong = next(n for n in range(1, 13) if n not in (own["Марс"], guest["Марс"]))
    assert kinds(f"Марс в {ordinals[wrong]} доме.") == ["house"]

    # Аспект внутри карты одной стороны — не находка, хотя между картами его нет.
    between = {frozenset((a["a_ru"], a["b_ru"])) for a in calc["aspects"]}
    inner = next(
        a for a in natal_b["aspects"]
        if frozenset((a["p1_ru"], a["p2_ru"])) not in between
        and a["type_ru"] == "квадрат" and "Северный узел" not in (a["p1_ru"], a["p2_ru"])
        and "Середина неба" not in (a["p1_ru"], a["p2_ru"])
    )
    forms = {"Солнце": "Солнцу", "Луна": "Луне", "Меркурий": "Меркурию", "Венера": "Венере",
             "Марс": "Марсу", "Юпитер": "Юпитеру", "Сатурн": "Сатурну", "Уран": "Урану",
             "Нептун": "Нептуну", "Плутон": "Плутону", "Хирон": "Хирону", "Асцендент": "Асценденту"}
    assert kinds(f"У Маши {inner['p1_ru']} в квадрате к {forms[inner['p2_ru']]}.") == []


# --- отбор фактов тизера ---------------------------------------------------------


async def test_teaser_facts_drop_moon_of_side_without_time() -> None:
    item = await _pair_item()
    pair_data = item.order.pair_data
    calc = vip_pair.synastry(item.chart, pair_data)
    facts = pair_teaser.facts(calc)

    anchors = facts["strong"] + facts["spark"]
    # Луна партнёра без времени в опоры тизера не идёт.
    assert not any(a["b"] == "Moon" for a in anchors)
    # Опора — контакт с личной планетой хотя бы с одной стороны.
    assert all({a["a"], a["b"]} & {"Sun", "Moon", "Venus", "Mars"} for a in anchors)


def _teaser_facts(aspects: list[dict]) -> dict:
    calc = {
        "meta": {"a": {"time_known": True}, "b": {"time_known": True}},
        "aspects": aspects,
    }
    return pair_teaser.facts(calc)


def test_teaser_anchors_are_two_different_contacts() -> None:
    def asp(a: str, kind: str, b: str) -> dict:
        return {"a": a, "b": b, "type": kind}

    # Список идёт по весу: опорой становится первый подходящий сверху.
    facts = _teaser_facts(
        [
            asp("Pluto", "trine", "Sun"),  # не личная пара — мимо
            asp("Venus", "square", "Mars"),
            asp("Saturn", "conjunction", "Sun"),
            asp("Mars", "trine", "Sun"),
            asp("Venus", "conjunction", "Moon"),
        ]
    )
    assert [(a["a"], a["type"]) for a in facts["strong"]] == [("Mars", "trine"), ("Venus", "conjunction")]
    assert [(a["a"], a["type"]) for a in facts["spark"]] == [("Venus", "square"), ("Saturn", "conjunction")]
    # Один контакт в оба списка не попадает.
    assert not [a for a in facts["strong"] if a in facts["spark"]]

    # Гармоничного нет — список пуст, квадрат в «Что держит» не подставляется.
    facts = _teaser_facts([asp("Venus", "square", "Mars")])
    assert facts["strong"] == [] and len(facts["spark"]) == 1

    # В списке не больше трёх контактов — по числу пунктов раздела.
    facts = _teaser_facts([asp("Venus", "square", "Mars")] * 5)
    assert len(facts["spark"]) == 3


def test_teaser_prompt_has_no_numbers_to_quote() -> None:
    natal_a = compute_natal(BIRTH, PLACE.lat, PLACE.lon, PLACE.tz)
    natal_b = compute_natal(datetime(1990, 5, 11, 12, 0), PLACE.lat, PLACE.lon, PLACE.tz)  # noqa: DTZ001
    calc = compute_synastry(natal_a, natal_b, a_time_known=True, b_time_known=False)
    message = teaser_prompt.build_user(pair_teaser.facts(calc), name_b="Маша")
    # Ни орбов, ни градусов, ни процентов: процитировать модели нечего.
    assert "°" not in message and "%" not in message and "орб" not in message
    assert "Партнёра зовут Маша." in message
    # Планет во входе нет вовсе — только роли сторон.
    for planet in ("Солнц", "Лун", "Венер", "Марс", "Сатурн"):
        assert planet not in message
    # Без имени пол партнёра неизвестен — местоимения закрыты строкой во входе.
    nameless = teaser_prompt.build_user(pair_teaser.facts(calc))
    assert "пола партнёра мы не знаем" in nameless


def test_teaser_parse() -> None:
    good = (
        '```json\n{"hook": "молчат  третий день.", "spark": ["а", "б", "в", "г"], '
        '"strong": ["д"], "outcome": "лёгкой не будет. Если один уступит, получится."}\n```'
    )
    assert teaser_prompt.parse(good) == {
        "hook": "молчат третий день", "spark": ["А", "Б", "В"], "strong": ["Д"],
        # От итога остаётся первая фраза: условие «если…» — уже платный ответ.
        "outcome": "Лёгкой не будет.",
    }
    bad = '{"hook": "а", "spark": ["совпадение 90%"], "strong": ["д"], "outcome": "е"}'
    assert teaser_prompt.parse(bad) is None
    assert teaser_prompt.parse('{"hook": "а", "spark": [], "strong": ["д"], "outcome": "е"}') is None
    assert teaser_prompt.parse('{"hook": "а", "spark": ["б"]}') is None
    assert teaser_prompt.parse("просто текст") is None
