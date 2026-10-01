"""Карточка человека: разбор строки поиска и подтягивание имени из VK."""

from __future__ import annotations

import pytest

from app.models import Platform, User
from app.services import admin as admin_service
from app.services import users as users_service


@pytest.mark.parametrize(
    ("query", "screen"),
    [
        ("https://vk.com/id123", "id123"),
        ("vk.com/durov", "durov"),
        ("m.vk.com/durov?w=wall1_2", "durov"),
        ("https://vk.ru/id5", "id5"),
    ],
)
def test_vk_link_extracts_screen(query: str, screen: str) -> None:
    match = admin_service._VK_LINK.match(query)
    assert match is not None and match.group(1) == screen


def test_name_is_not_a_link() -> None:
    assert admin_service._VK_LINK.match("Иванов Сергей") is None


@pytest.mark.parametrize(
    ("screen", "expected"),
    [("123456", "123456"), ("id123456", "123456"), ("durov", None), ("idol", None)],
)
def test_plain_vk_id(screen: str, expected: str | None) -> None:
    assert admin_service._plain_vk_id(screen) == expected


async def test_fill_vk_name_sets_name(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_names(ids):
        return {ids[0]: "Иван Петров"}

    monkeypatch.setattr(users_service.vk_api, "get_full_names", fake_names)
    user = User(platform=Platform.VK, external_id="42")
    await users_service.fill_vk_name(user)
    assert user.platform_username == "Иван Петров"


async def test_fill_vk_name_skips_known(monkeypatch: pytest.MonkeyPatch) -> None:
    async def must_not_call(ids):
        raise AssertionError("в VK ходить не должны")

    monkeypatch.setattr(users_service.vk_api, "get_full_names", must_not_call)
    user = User(platform=Platform.VK, external_id="42", platform_username="Уже есть")
    await users_service.fill_vk_name(user)
    web_user = User(platform=Platform.WEB, external_id="uuid")
    await users_service.fill_vk_name(web_user)
    assert web_user.platform_username is None


def test_platform_username_is_cut_to_column() -> None:
    user = User(platform=Platform.VK, external_id="1")
    users_service.set_platform_username(user, "я" * 100)
    assert len(user.platform_username) == 64
