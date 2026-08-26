"""Проверки рассылки (app/services/broadcast.py).

Базы в тестах нет, поэтому проверяется то, что от неё не зависит: чтение
знака из расчёта, склейка письма, стабильность random_id и разбор ответа VK
в run() с подменёнными зависимостями.

Главный из них — тест на форму calc. Ошибка чтения знака ничего не роняет:
рассылка молча пропускает всех получателей и выглядит как «никто не
подписан». Именно так она и жила до первой живой отправки 25.08.2026.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.services import broadcast
from app.vk import api as vk_api

TODAY = date(2026, 8, 25)

# Форма записи из боевой базы: расчёт под ключом natal, планеты списком.
CALC = {
    "natal": {
        "planets": [
            {"name": "Sun", "name_ru": "Солнце", "sign": "Leo", "sign_ru": "Лев"},
            {"name": "Moon", "name_ru": "Луна", "sign": "Can", "sign_ru": "Рак"},
        ]
    }
}


# --- Знак Солнца ----------------------------------------------------------


def test_sun_sign_boevaya_forma() -> None:
    assert broadcast.sun_sign(CALC) == "Лев"


@pytest.mark.parametrize(
    "calc",
    [
        {},
        {"natal": None},
        {"natal": {}},
        {"natal": {"planets": []}},
        # Планеты в корне — форма, которой в базе нет; читать её нельзя,
        # иначе ошибка чтения снова притворится пустой аудиторией.
        {"planets": [{"name": "Sun", "sign_ru": "Лев"}]},
        {"natal": {"planets": [{"name": "Moon", "sign_ru": "Рак"}]}},
    ],
)
def test_sun_sign_net_znaka(calc: dict) -> None:
    assert broadcast.sun_sign(calc) is None


# --- Письмо ---------------------------------------------------------------


def test_compose_sobiraet_pismo() -> None:
    letter = broadcast.compose("  Вступление про день. ", "Лев", "  Абзац знака.  ")
    assert letter == "Вступление про день.\n\nЛев\nАбзац знака."


# --- random_id ------------------------------------------------------------


def test_random_id_stabilen() -> None:
    """Повтор рассылки не должен давать второй копии письма."""
    assert broadcast._random_id(TODAY, "Лев") == broadcast._random_id(TODAY, "Лев")


def test_random_id_razlichaet_znak_i_datu() -> None:
    assert broadcast._random_id(TODAY, "Лев") != broadcast._random_id(TODAY, "Дева")
    assert broadcast._random_id(TODAY, "Лев") != broadcast._random_id(date(2026, 8, 26), "Лев")


def test_random_id_v_predelah_znakovogo_int32() -> None:
    """VK принимает знаковое 32-битное число: старший бит обязан быть снят."""
    for sign in ("Лев", "Дева", "Рыбы", "Овен"):
        value = broadcast._random_id(TODAY, sign)
        assert 0 <= value <= 0x7FFF_FFFF


# --- Отправка -------------------------------------------------------------


class FakeSession:
    """Заглушка сессии: запоминает добавленное и выполненное, в базу не ходит."""

    def __init__(self) -> None:
        self.added: list[object] = []
        self.executed: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def execute(self, statement: object) -> None:
        self.executed.append(statement)


def _event_names(session: FakeSession) -> list[str]:
    return [getattr(obj, "name", "") for obj in session.added]


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch):
    """Подменяет всё, что ходит наружу: база, транспорт VK и сборка кнопки.

    Возвращает функцию, которой тест задаёт получателей и ответ VK.
    """

    def setup(people: list[broadcast.Recipient], results: list[vk_api.SendResult] | Exception):
        async def fake_letters(session, target_date):
            return {sign: f"письмо для {sign}" for sign in ("Лев", "Дева")}

        async def fake_recipients(session, target_date):
            return people

        async def fake_send(user_ids, text, *, random_id, keyboard=None):
            if isinstance(results, Exception):
                raise results
            return [r for r in results if r.user_id in user_ids]

        monkeypatch.setattr(broadcast, "letters_for", fake_letters)
        monkeypatch.setattr(broadcast, "recipients", fake_recipients)
        monkeypatch.setattr(vk_api, "send_messages", fake_send)
        monkeypatch.setattr(vk_api, "open_app_keyboard", lambda *a, **kw: "{}")

    return setup


async def test_run_dostavka(stub) -> None:
    people = [
        broadcast.Recipient(user_id=1, vk_id=101, sign="Лев"),
        broadcast.Recipient(user_id=2, vk_id=102, sign="Лев"),
    ]
    stub(
        people,
        [
            vk_api.SendResult(user_id=101, message_id=11),
            vk_api.SendResult(user_id=102, message_id=12),
        ],
    )
    session = FakeSession()

    report = await broadcast.run(session, TODAY)

    assert (report.delivered, report.failed) == (2, 0)
    assert report.aborted is None
    assert _event_names(session) == ["broadcast_sent", "broadcast_sent"]


async def test_run_otkaz_901_gasit_razreshenie(stub) -> None:
    people = [broadcast.Recipient(user_id=1, vk_id=101, sign="Лев")]
    stub(
        people,
        [vk_api.SendResult(user_id=101, error_code=901, error_text="не разрешал")],
    )
    session = FakeSession()

    report = await broadcast.run(session, TODAY)

    assert (report.delivered, report.failed) == (0, 1)
    assert report.errors == {901: 1}
    # Разрешение гасится одним UPDATE, и рядом пишется событие оттока.
    assert len(session.executed) == 1
    assert _event_names(session) == ["broadcast_failed", "group_messages_denied"]


@pytest.mark.parametrize("code", [900, 902])
async def test_run_nedostavlyaemye_gasyat_dostavku(stub, code: int) -> None:
    """900 (чёрный список) и 902 (приватность) ставят bot_blocked_at.

    Событие group_messages_denied при этом НЕ пишется: наше разрешение
    осталось в силе, погашена доставка, а это разные вещи.
    """
    people = [broadcast.Recipient(user_id=1, vk_id=101, sign="Лев")]
    stub(people, [vk_api.SendResult(user_id=101, error_code=code)])
    session = FakeSession()

    report = await broadcast.run(session, TODAY)

    assert report.errors == {code: 1}
    assert len(session.executed) == 1
    assert _event_names(session) == ["broadcast_failed"]


async def test_run_901_i_900_gasyat_raznoe(stub) -> None:
    """Два кода в одной пачке разводятся по разным UPDATE, а не сливаются."""
    people = [
        broadcast.Recipient(user_id=1, vk_id=101, sign="Лев"),
        broadcast.Recipient(user_id=2, vk_id=102, sign="Лев"),
    ]
    stub(
        people,
        [
            vk_api.SendResult(user_id=101, error_code=901),
            vk_api.SendResult(user_id=102, error_code=900),
        ],
    )
    session = FakeSession()

    report = await broadcast.run(session, TODAY)

    assert report.errors == {901: 1, 900: 1}
    # Два UPDATE: разрешение первому, недоставляемость второму.
    assert len(session.executed) == 2
    assert _event_names(session) == [
        "broadcast_failed",
        "broadcast_failed",
        "group_messages_denied",
    ]


async def test_run_obryv_ne_teryaet_otmetok(stub) -> None:
    """VkError не бросается наверх: иначе откатятся отметки об ушедших письмах."""
    people = [broadcast.Recipient(user_id=1, vk_id=101, sign="Лев")]
    stub(people, vk_api.VkError("ключ протух"))
    session = FakeSession()

    report = await broadcast.run(session, TODAY)

    assert report.aborted == "ключ протух"
    assert (report.delivered, report.failed) == (0, 0)


async def test_run_dry_run_nichego_ne_shlet(stub, monkeypatch: pytest.MonkeyPatch) -> None:
    people = [broadcast.Recipient(user_id=1, vk_id=101, sign="Лев")]
    stub(people, [])

    async def explode(*args, **kwargs):
        raise AssertionError("dry_run не должен отправлять")

    monkeypatch.setattr(vk_api, "send_messages", explode)
    session = FakeSession()

    report = await broadcast.run(session, TODAY, dry_run=True)

    assert report.delivered == 1
    assert session.added == []
