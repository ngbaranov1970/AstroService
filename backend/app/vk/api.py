"""Отправка сообщений от имени сообщества: метод messages.send.

Место в слоях: services/рассылка → ЭТОТ ФАЙЛ → api.vk.com. Ни базы, ни
наших пользователей здесь нет: на вход идут числовые идентификаторы VK,
на выход — что ответил VK по каждому из них.

ОТКАЗ КОНКРЕТНОМУ ЧЕЛОВЕКУ — ЭТО ДАННЫЕ, А НЕ ИСКЛЮЧЕНИЕ

Один вызов уходит сразу на сотню адресатов, и VK отвечает по каждому
отдельно: кому-то message_id, кому-то ошибка 901 «не разрешал писать».
Такой ответ — успех вызова, и превращать его в исключение нельзя: упало бы
письмо остальным девяноста девяти. Поэтому SendResult отдаётся списком, а
решение (погасить разрешение в базе) принимает слой выше — у него есть
база, у нас её нет.

Исключение VkError бросается только там, где сорвался ВЕСЬ вызов: сеть,
5xx, протухший ключ сообщества.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

BASE_URL = "https://api.vk.com/method/"

# Версия API фиксирована намеренно: VK меняет форму ответов между версиями
# (в 5.103 поля message_new переехали в object.message), и «последняя» —
# это ответ, который однажды приедет другим без единой правки у нас.
API_VERSION = "5.199"

# Больше сотни идентификаторов messages.send не принимает — это предел
# метода, а не наша осторожность.
MAX_RECIPIENTS = 100

# Пауза между пачками. Ключу сообщества VK разрешает около 20 запросов в
# секунду, так что 0.34 с — запас втрое; смысл не в лимите, а в том, чтобы
# рассылка на тысячу человек не выглядела всплеском.
PAUSE_BETWEEN_CHUNKS = 0.34

TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)

# Сколько раз повторяем вызов, сорвавшийся целиком.
MAX_ATTEMPTS = 3

# --- Коды ошибок VK, которые разбирает вызывающий -------------------------
# Полный список — dev.vk.ru/ru/method/messages.send. Здесь только те, что
# меняют наши данные или поведение.

# Не разрешал сообществу писать (или отозвал разрешение, а событие до нас
# не доехало). Главный код рассылки: по нему гасится vk_messages_allowed_at.
ERROR_NO_PERMISSION = 901

# Запрещают настройки приватности человека. Отличается от 901 тем, что от
# нашего разрешения не зависит и сам собой не пройдёт.
ERROR_PRIVACY = 902

# Мы у него в чёрном списке.
ERROR_BLACKLISTED = 900

# Спам-ограничение НА НАС. Не про адресата: продолжать рассылку в этот
# момент — верный путь к блокировке сообщества.
ERROR_SPAM_LIMIT = 984

# Слишком много запросов в секунду. Единственный код, на котором есть смысл
# повторять: он про темп, а не про содержание.
ERROR_TOO_MANY = 6


class VkError(RuntimeError):
    """Вызов не состоялся целиком: сеть, 5xx, негодный ключ сообщества."""


@dataclass(slots=True)
class SendResult:
    """Что VK ответил про ОДНОГО адресата.

    error_code=None — доставлено, в message_id лежит идентификатор письма.
    Иначе message_id пуст, а в error_code код из таблицы выше.
    """

    user_id: int
    message_id: int | None = None
    error_code: int | None = None
    error_text: str = ""

    @property
    def delivered(self) -> bool:
        return self.error_code is None


@lru_cache(maxsize=1)
def _client() -> httpx.AsyncClient:
    """Один AsyncClient на процесс; ключ читается лениво (см. config.py)."""
    token = get_settings().vk_group_secure_key
    # Ключа доступа сообщества в .env нет, слать нечем — падаем здесь, иначе
    # разбираться придётся по их ошибке 5 «authorization failed».
    if token is None:
        raise VkError("Рассылка выключена: в .env нет VK_GROUP_SECURE_KEY")
    return httpx.AsyncClient(
        base_url=BASE_URL,
        timeout=TIMEOUT,
        # Ключ заголовком, хотя VK принимает и параметр access_token в теле:
        # параметры запросов оседают в логах прокси целиком, заголовок — нет.
        # Оба способа проверены на groups.getById 24.08.2026.
        params={"v": API_VERSION},
        headers={"Authorization": f"Bearer {token.get_secret_value()}"},
    )


async def close_client() -> None:
    """Закрыть соединения на shutdown приложения (lifespan FastAPI)."""
    if _client.cache_info().currsize:
        await _client().aclose()
        _client.cache_clear()


def open_app_keyboard(label: str, *, launch_hash: str = "") -> str | None:
    """Клавиатура из одной кнопки «открыть наше приложение» — JSON для messages.send.

    launch_hash уезжает в строку запуска приложения: это единственный способ
    сказать открытому экрану, из какого письма пришли.

    None — в .env нет VK_APP_ID или VK_GROUP_ID. Не ошибка: письмо без кнопки
    дойдёт, а упавшая на полпути рассылка — нет.
    """
    settings = get_settings()
    if settings.vk_app_id is None or settings.vk_group_id is None:
        logger.warning("Кнопка приложения не собрана: нет VK_APP_ID или VK_GROUP_ID")
        return None

    action: dict[str, Any] = {
        "type": "open_app",
        "label": label,
        "app_id": settings.vk_app_id,
        # Минус обязателен: идентификатор сообщества у VK отрицательный, и с
        # положительным приложение откроется вне контекста группы.
        "owner_id": -settings.vk_group_id,
    }
    if launch_hash:
        action["hash"] = launch_hash

    # inline: кнопка принадлежит своему письму и остаётся при нём в истории.
    # Обычная клавиатура одна на весь диалог — вчерашняя перетёрла бы сегодняшнюю.
    keyboard = {"inline": True, "buttons": [[{"action": action}]]}
    # ensure_ascii=False — VK принимает UTF-8, а экранированная кириллица
    # превращает надпись в \u-мусор при чтении логов и отладке.
    return json.dumps(keyboard, ensure_ascii=False)


async def send_messages(
    user_ids: list[int], text: str, *, random_id: int, keyboard: str | None = None
) -> list[SendResult]:
    """Отправить один и тот же текст пачке людей. Результат — на каждого.

    random_id задаёт вызывающий и обязан делать это ОСМЫСЛЕННО: VK хранит
    его час и отбрасывает повторную отправку с тем же значением. Число,
    производное от «кому и за какой день», делает перезапуск рассылки
    безвредным; случайное превращает его во вторую копию письма.

    keyboard — готовый JSON от open_app_keyboard(); None отправляет письмо
    без кнопок.

    Список режется на пачки по 100 — предел метода. Бросает VkError, если
    сорвался весь вызов; отказ отдельному человеку возвращается в списке.
    """
    results: list[SendResult] = []
    for start in range(0, len(user_ids), MAX_RECIPIENTS):
        chunk = user_ids[start : start + MAX_RECIPIENTS]
        results.extend(await _send_chunk(chunk, text, random_id, keyboard))
        # Пауза только между пачками, не после последней.
        if start + MAX_RECIPIENTS < len(user_ids):
            await asyncio.sleep(PAUSE_BETWEEN_CHUNKS)
    return results


async def _send_chunk(
    user_ids: list[int], text: str, random_id: int, keyboard: str | None = None
) -> list[SendResult]:
    """Один вызов messages.send на пачку до ста человек."""
    data: dict[str, Any] = {
        "user_ids": ",".join(str(uid) for uid in user_ids),
        "message": text,
        "random_id": random_id,
        # Упоминания вида @id123 в тексте не подсвечиваем: текст пишет LLM,
        # и случайная «собака» не должна дёргать посторонних людей.
        "disable_mentions": 1,
    }
    # Пустой keyboard не отправляем вовсе: VK на "" отвечает ошибкой разбора.
    if keyboard:
        data["keyboard"] = keyboard
    payload = await _request("messages.send", data)

    # На пачку VK отвечает массивом объектов, по одному на адресата. Иногда
    # (например, когда в user_ids один человек) — просто числом message_id.
    if isinstance(payload, int):
        return [SendResult(user_id=user_ids[0], message_id=payload)]

    results: list[SendResult] = []
    for item in payload if isinstance(payload, list) else []:
        # peer_id для личных сообщений совпадает с идентификатором человека.
        user_id = item.get("peer_id") or item.get("user_id")
        error = item.get("error") or {}
        results.append(
            SendResult(
                user_id=int(user_id) if user_id else 0,
                message_id=item.get("message_id"),
                error_code=error.get("code"),
                error_text=str(error.get("description", "")),
            )
        )
    return results


async def _request(method: str, data: dict[str, Any]) -> Any:
    """Вызвать метод API и вернуть содержимое поля response.

    Повторяет попытку на сетевом сбое, 5xx и коде 6 («слишком много
    запросов в секунду») — на всём, что зависит от момента, а не от того,
    что мы просим. Бросает VkError, когда попытки кончились.
    """
    last_error: str = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = await _client().post(method, data=data)
        except httpx.HTTPError as e:
            # Текст httpx содержит адрес, но не тело запроса — ключа в нём нет.
            last_error = f"сеть: {e}"
        else:
            if resp.status_code >= 500:
                last_error = f"HTTP {resp.status_code}"
            else:
                try:
                    body = resp.json()
                except ValueError as e:
                    raise VkError(f"VK вернул не JSON: {e}") from e

                error = body.get("error")
                if error is None:
                    return body.get("response")

                code = error.get("error_code")
                message = error.get("error_msg", "")
                if code != ERROR_TOO_MANY:
                    # Ошибка про сам запрос: негодный ключ, нет прав,
                    # спам-ограничение. Повтор ничего не изменит.
                    raise VkError(f"VK {method}: ошибка {code} — {message}")
                last_error = f"ошибка {code} — {message}"

        # Растущая пауза: 0.5, 1, 2 секунды. Повторять сразу бессмысленно
        # там, где нас просят сбавить темп.
        if attempt < MAX_ATTEMPTS:
            await asyncio.sleep(0.5 * 2 ** (attempt - 1))
            logger.warning("VK %s: попытка %s не удалась (%s)", method, attempt, last_error)

    raise VkError(f"VK {method} недоступен после {MAX_ATTEMPTS} попыток: {last_error}")
