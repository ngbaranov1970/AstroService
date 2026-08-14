"""Транспорт к ЮKassa: создать платёж, спросить его состояние. Всё.

Слои оплаты в проекте разделены так же, как слои LLM:
- yookassa.py (этот файл) — HTTP к api.yookassa.ru, ничего про наши таблицы;
- services/orders.py — заказы: прайс, создание строки, зачисление денег;
- web/pay.py — страница оплаты, возврат человека и приём вебхука.

Библиотеку `yookassa` не берём по той же причине, что и SDK OpenAI: три
метода на httpx проще, чем зависимость с синхронным requests внутри — она
заблокировала бы event loop ровно там, где мы ждём чужой сервер.

ГЛАВНОЕ: УВЕДОМЛЕНИЮ ВЕБХУКА ВЕРИТЬ НЕЛЬЗЯ

ЮKassa не подписывает уведомления — тело приходит открытым текстом, и
подделать его может любой, кто узнал адрес вебхука. Их собственная
рекомендация — фильтровать по списку IP, но список меняется на их стороне
и молча перестанет пропускать настоящие уведомления. Поэтому здесь
уведомление служит только СИГНАЛОМ «сходи посмотри», а состояние платежа
берётся ответом на наш GET /payments/{id}: он идёт по TLS на их домен с
нашим ключом, подделать его нельзя.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

BASE_URL = "https://api.yookassa.ru/v3"

# read=30 не с потолка: ЮKassa сама держит ответ до 30 секунд и только потом
# отвечает 500. Таймаут короче их собственного означал бы, что мы рвём
# соединение ровно на медленных, но успешных платежах.
TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)

# Повторяем только то, что имеет шанс пройти со следующего раза. 4xx не
# повторяем никогда: 401 (не тот ключ) и 400 (кривой запрос) от повтора не
# вылечатся, а на платёжном API лишние попытки — лишний риск.
#
# 500 в этом списке при создании платежа безопасен ровно потому, что повтор
# идёт с тем же Idempotence-Key: их 500 НЕ означает «операция не прошла»
# (прямая цитата их документации), и без ключа второй запрос мог бы создать
# второй платёж на ту же сумму.
RETRY_STATUS = {429, 500, 502, 503, 504}
RETRY_DELAY_S = 2.0

# 202 — не ошибка: «запрос принят, но ещё не обработан, повторите с тем же
# Idempotence-Key через retry_after миллисекунд». Их значение уважаем, но
# ограничиваем сверху: за обработчиком ждёт человек, а не фоновая задача.
RETRY_AFTER_MAX_S = 5.0

# Три попытки, а не две: одна тратится на 202, и без запаса медленный, но
# успешный платёж превращался бы в ошибку на глазах у плательщика.
MAX_ATTEMPTS = 3

# Статусы платежа у ЮKassa. WAITING_FOR_CAPTURE у нас не возникает —
# платежи создаём с capture=true (товар цифровой, придерживать нечего).
STATUS_SUCCEEDED = "succeeded"
STATUS_CANCELED = "canceled"


class PaymentError(RuntimeError):
    """ЮKassa не ответила или ответила неожиданным (сеть, статус, формат)."""


@dataclass(frozen=True, slots=True)
class Payment:
    """Платёж на стороне ЮKassa — ровно те поля, которые нам нужны."""

    id: str
    status: str
    # paid отделён от status намеренно: succeeded+paid=false у них означает
    # оплату, отменённую до зачисления. Деньги открывают отчёт, а не статус.
    paid: bool
    amount_rub: str
    # Куда отправить человека платить. None, если платёж уже не ждёт оплаты.
    confirmation_url: str | None
    # Ответ целиком — он уезжает в orders.provider_data: при споре «мы вам не
    # платили» на стол кладут именно их формат, а не наш пересказ.
    raw: dict[str, Any]


@lru_cache(maxsize=1)
def _client() -> httpx.AsyncClient:
    """Один AsyncClient на процесс; ключи читаются лениво (см. config.py)."""
    settings = get_settings()
    shop_id = settings.yookassa_shop_id
    secret_key = settings.yookassa_secret_key
    # Ключей для подключения к ЮKassa в .env нет, платить нечем — вызываем
    # ошибку здесь, иначе разбираться придётся по их 401.
    if shop_id is None or secret_key is None:
        raise PaymentError(
            "Приём платежей выключен: в .env нет YOOKASSA_SHOP_ID "
            "или YOOKASSA_SECRET_KEY"
        )
    return httpx.AsyncClient(
        base_url=BASE_URL,
        timeout=TIMEOUT,
        # Basic auth парой shopId:secretKey — так устроен их API.
        auth=(shop_id, secret_key.get_secret_value()),
    )


async def close_client() -> None:
    """Закрыть соединения на shutdown приложения (lifespan FastAPI)."""
    if _client.cache_info().currsize:
        await _client().aclose()
        _client.cache_clear()


async def create_payment(
    *,
    amount_rub: str,
    description: str,
    return_url: str,
    idempotence_key: str,
    metadata: dict[str, str] | None = None,
) -> Payment:
    """Завести платёж и получить адрес формы оплаты.

    idempotence_key обязателен и должен быть СТАБИЛЬНЫМ для одного заказа:
    по нему ЮKassa на повторный запрос возвращает тот же платёж вместо
    второго на ту же сумму (двойной клик, ретрай, перезагрузка страницы).
    Ключ живёт у них около суток.

    description видит человек в форме оплаты и в выписке банка — туда идёт
    название товара и ничего больше: ни имени, ни даты рождения.
    Бросает PaymentError.
    """
    payload: dict[str, Any] = {
        "amount": {"value": amount_rub, "currency": "RUB"},
        # capture=true — списываем сразу, без двухстадийности: товар цифровой
        # и всегда «в наличии» (см. OrderStatus в app/models.py).
        "capture": True,
        "confirmation": {"type": "redirect", "return_url": return_url},
        "description": description[:128],
    }
    if metadata:
        payload["metadata"] = metadata

    data = await _request(
        "POST",
        "/payments",
        json=payload,
        headers={"Idempotence-Key": idempotence_key},
    )
    payment = _parse(data)
    logger.info(
        "ЮKassa создала платёж %s на %s ₽, статус %s",
        payment.id,
        payment.amount_rub,
        payment.status,
    )
    return payment


async def get_payment(payment_id: str) -> Payment:
    """Состояние платежа по их id — источник истины при разборе вебхука.

    Бросает PaymentError, в том числе на 404 (чужой или выдуманный id).
    """
    return _parse(await _request("GET", f"/payments/{payment_id}"))


def notification_payment_id(body: dict[str, Any]) -> str | None:
    """Достать id платежа из тела уведомления; None — тело не про платёж.

    Больше из уведомления не берётся НИЧЕГО (ни статуса, ни суммы): оно не
    подписано, см. шапку модуля. Возвращённый id проверяется get_payment().
    """
    if body.get("type") != "notification":
        return None
    if not str(body.get("event", "")).startswith("payment."):
        return None
    payment_id = (body.get("object") or {}).get("id")
    # Их id — uuid; ограничение длины тут не про красоту, а про то, что
    # значение уходит в URL нашего же запроса и в колонку varchar(64).
    if isinstance(payment_id, str) and 0 < len(payment_id) <= 64:
        return payment_id
    return None


async def _request(
    method: str,
    path: str,
    *,
    json: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Один вызов API с повторами на 202, 429/5xx и сеть. Бросает PaymentError.

    Повтор всегда идёт ТЕМ ЖЕ запросом с тем же Idempotence-Key — только
    поэтому он безопасен на деньгах: ЮKassa опознаёт ключ и возвращает уже
    созданный платёж вместо второго (гарантия действует сутки).
    """
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        delay = RETRY_DELAY_S
        try:
            resp = await _client().request(method, path, json=json, headers=headers)
        except httpx.HTTPError as e:
            last_error = e
            logger.warning(
                "Попытка %d/%d: ЮKassa недоступна (%s: %s)",
                attempt, MAX_ATTEMPTS, type(e).__name__, e,
            )
        else:
            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError as e:
                    raise PaymentError(f"ЮKassa вернула не JSON: {e}") from e

            if resp.status_code == 202:
                delay = _retry_after(resp)
                last_error = PaymentError("ЮKassa: запрос принят, но ещё в обработке")
                logger.warning(
                    "Попытка %d/%d: ЮKassa обрабатывает запрос, повтор через %.1f с",
                    attempt, MAX_ATTEMPTS, delay,
                )
            else:
                # Тело ошибки у них информативное (описание причины) и ПДн не
                # содержит — мы их туда и не передаём.
                last_error = PaymentError(
                    f"ЮKassa HTTP {resp.status_code}: {resp.text[:300]}"
                )
                if resp.status_code not in RETRY_STATUS:
                    break
                logger.warning(
                    "Попытка %d/%d: ЮKassa вернула %d, повтор через %.1f с",
                    attempt, MAX_ATTEMPTS, resp.status_code, delay,
                )
        if attempt < MAX_ATTEMPTS:
            await asyncio.sleep(delay)

    logger.error("Запрос %s %s к ЮKassa не прошёл: %s", method, path, last_error)
    raise PaymentError(f"ЮKassa недоступна: {last_error}") from last_error


def _retry_after(resp: httpx.Response) -> float:
    """Пауза перед повтором из тела ответа 202 (у них — миллисекунды)."""
    try:
        value = float(resp.json().get("retry_after", 0)) / 1000
    except (ValueError, TypeError, AttributeError):
        return RETRY_DELAY_S
    # Ноль и мусор — обычный дефолт; сверху потолок, потому что их совет
    # «зайдите через минуту» человек перед платёжной формой не переживёт.
    return min(value, RETRY_AFTER_MAX_S) if value > 0 else RETRY_DELAY_S


def _parse(data: dict[str, Any]) -> Payment:
    """Ответ API → Payment; неожиданный формат — тоже PaymentError."""
    try:
        return Payment(
            id=data["id"],
            status=data["status"],
            paid=bool(data.get("paid")),
            amount_rub=data["amount"]["value"],
            confirmation_url=(data.get("confirmation") or {}).get("confirmation_url"),
            raw=data,
        )
    except (KeyError, TypeError) as e:
        raise PaymentError(f"Неожиданный формат ответа ЮKassa: {e}") from e
