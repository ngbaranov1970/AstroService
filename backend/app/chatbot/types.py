"""Сообщения ядра бота: вход без привязки к VK и ответ с кнопками.

VK-обёртка (app/vk/bot.py) потом возьмёт сырое событие VK и сделает из него
Incoming, а из нашего Reply — вызов «отправить сообщение».
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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
    buttons: list[list[AnyButton]] = field(default_factory=list)
    inline: bool = False
