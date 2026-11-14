"""Сообщения ядра бота: вход без привязки к VK и ответ с кнопками.

VK-обёртка (app/vk/bot.py) потом возьмёт сырое событие VK и сделает из него
Incoming, а из нашего Reply — вызов «отправить сообщение».
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

# Проверка «подписан ли на сообщество» от адаптера площадки. None в ответе —
# площадка не ответила; зовётся только на входе в КАРТУ и ПАРУ (flows/gate.py).
MemberCheck = Callable[[], Awaitable[bool | None]]


@dataclass(frozen=True, slots=True)
class Incoming:
    """Одно входящее сообщение от человека на любой площадке."""

    platform: str
    external_id: str
    text: str
    payload: dict[str, Any] | None = None
    ref: str | None = None


@dataclass(frozen=True, slots=True)
class Button:
    """Текстовая кнопка: подпись + payload, который вернётся при нажатии."""

    label: str
    payload: dict[str, Any]
    color: str = "secondary"


@dataclass(frozen=True, slots=True)
class LinkButton:
    """Кнопка-ссылка наружу (оплата, vk.me и т.п.)."""

    label: str
    url: str


@dataclass(frozen=True, slots=True)
class AppButton:
    """Кнопка открытия Mini App на hash-цели (#pair, #natal, …)."""

    label: str
    hash: str


AnyButton = Button | LinkButton | AppButton


@dataclass(frozen=True, slots=True)
class Reply:
    """Один исходящий ответ: текст и опциональная клавиатура рядами кнопок."""

    text: str
    # Sequence, а не list: list инвариантен, и list[list[Button]] сюда не прошёл бы.
    buttons: Sequence[Sequence[AnyButton]] = field(default_factory=list)
    inline: bool = False
    # PNG к сообщению (тизер пары). Адаптер грузит его сам; не вышло — шлёт текст.
    image: bytes | None = None


@dataclass(frozen=True, slots=True)
class Job:
    """Долгая работа после отправки предыдущих ответов (разбор — 30–60 с).

    Адаптер сначала шлёт Reply до Job, потом зовёт router.run_job в новой
    сессии и шлёт уже его ответы — человек не ждёт минуту в тишине.
    """

    name: str


Outgoing = Reply | Job
