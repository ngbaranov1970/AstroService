"""Вход через VK ID: ссылка на авторизацию и обмен кода на `user_id`.

Место в слоях: маршруты app/web/routes/vkid.py → ЭТОТ ФАЙЛ → id.vk.ru.
Ни базы, ни FastAPI здесь нет: на вход строки, на выход строки. Та же
граница, что у соседнего api.py и у services/auth.py.

Не путать с api.py: там мы ДЕЙСТВУЕМ от имени сообщества по его ключу, здесь
проверяем чужое утверждение о личности. Приложение тоже другое — VK ID, не
Mini App (docs/astro-context.md, «Веб-вход через VK ID»).
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

AUTHORIZE_URL = "https://id.vk.ru/authorize"
TOKEN_URL = "https://id.vk.ru/oauth2/auth"

# Путь возврата. Он же вбит в кабинете VK как доверенный redirect URL и
# сверяется там посимвольно — менять только парой с кабинетом.
CALLBACK_PATH = "/auth/vkid"

# Значение users.source для пришедших через веб-вход. Рядом с SOURCE_VK из
# services/auth.py по смыслу, но здесь: там про параметры запуска Mini App.
SOURCE_VK_ID = "vk_id"

TIMEOUT = httpx.Timeout(connect=10.0, read=15.0, write=10.0, pool=10.0)


class VkIdError(Exception):
    """Авторизация не состоялась: VK отказал, ответил не тем или не ответил."""


@dataclass(frozen=True)
class AuthStart:
    """Начало входа: куда отправить человека и что сохранить до его возврата.

    `verifier` и `state` обязан сберечь вызывающий — без первого не выйдет
    обменять код, по второму сверяется, что вернулись из нашей же попытки.
    """

    url: str
    verifier: str
    state: str


def _b64url(raw: bytes) -> str:
    """base64 url-safe без выравнивания — в этом виде PKCE и ждёт значения."""
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def redirect_uri() -> str:
    """Абсолютный адрес возврата. Без public_base_url — `RuntimeError`."""
    settings = get_settings()
    if settings.public_base_url is None:
        raise RuntimeError("PUBLIC_BASE_URL не задан — адрес возврата не собрать")
    return f"{settings.public_base_url.rstrip('/')}{CALLBACK_PATH}"


def build_authorize_url() -> AuthStart:
    """Собрать ссылку на окно входа VK ID и секреты этой попытки.

    Бросает `RuntimeError`, если VK ID не настроен в окружении.
    """
    settings = get_settings()
    if settings.vk_id_client_id is None:
        raise RuntimeError("VK_ID_CLIENT_ID не задан — вход через VK ID невозможен")

    # PKCE: наружу уходит только хеш (challenge), сам verifier остаётся у нас
    # и предъявляется при обмене. Это и заменяет client_secret — перехвативший
    # код в адресной строке обменять его не сможет.
    verifier = _b64url(secrets.token_bytes(64))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    state = secrets.token_urlsafe(32)

    params = {
        "response_type": "code",
        "client_id": settings.vk_id_client_id,
        "redirect_uri": redirect_uri(),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        # scope не передаём: нужен только идентификатор. Имя, пол и дату
        # рождения человек вводит сам, и они точнее профильных.
    }
    return AuthStart(
        url=f"{AUTHORIZE_URL}?{urlencode(params)}",
        verifier=verifier,
        state=state,
    )


async def exchange_code(code: str, device_id: str, verifier: str) -> str:
    """Обменять код возврата на идентификатор пользователя ВКонтакте.

    Возвращает `user_id` строкой — в таком виде он ложится в
    `users.external_id` рядом с `vk_user_id` из Mini App: это одно и то же
    число. Бросает `VkIdError` на любой отказ, `RuntimeError` — если VK ID
    не настроен. Токены доступа не возвращает: они нам не нужны.
    """
    settings = get_settings()
    if settings.vk_id_client_id is None:
        raise RuntimeError("VK_ID_CLIENT_ID не задан — вход через VK ID невозможен")

    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": verifier,
        "client_id": settings.vk_id_client_id,
        "device_id": device_id,
        "redirect_uri": redirect_uri(),
    }

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(TOKEN_URL, data=payload)
    except httpx.HTTPError as err:
        raise VkIdError(f"VK ID не ответил: {err}") from err

    # Повторов нет намеренно: код одноразовый и живёт минуты, а человек ждёт
    # у пустого экрана. Сорвалось — проще предложить нажать «Войти» снова.
    if response.status_code != httpx.codes.OK:
        # Тело в лог не пишем: там наш code_verifier и обрывки токенов.
        logger.warning("VK ID отклонил обмен кода: HTTP %s", response.status_code)
        raise VkIdError(f"VK ID ответил {response.status_code}")

    try:
        data = response.json()
    except ValueError as err:
        raise VkIdError("VK ID ответил не JSON") from err

    user_id = data.get("user_id")
    if user_id is None:
        # Ошибку VK кладёт в error/error_description этого же JSON.
        logger.warning("В ответе VK ID нет user_id: %s", data.get("error"))
        raise VkIdError("VK ID не вернул идентификатор пользователя")

    return str(user_id)
