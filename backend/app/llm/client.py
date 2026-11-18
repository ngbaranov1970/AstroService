"""Транспорт к LLM: один вызов чата, ничего больше.

Слои LLM в проекте разделены так:
- client.py (этот файл) — доставка запроса и разбор ответа. Не знает ни про
  астрологию, ни про промпты, ни про кеш.
- llm/prompts/ — версионируемые шаблоны промптов (обязательное требование
  CLAUDE.md: prompt_version входит в ключ кеша отчётов).
- services/reports.py — кеш готовых отчётов и лимит бесплатных генераций.

API DeepSeek совместим с OpenAI (POST /chat/completions), поэтому обходимся
httpx без SDK: меньше зависимостей, видно каждый байт запроса. Модель с «/»
в имени («anthropic/claude-opus-5.5») идёт через агрегатор Polza.ai тем же
форматом — VIP-разборы и сравнение моделей (scripts/compare_models.py).
OpenRouter с российского сервера недоступен (403), поэтому Polza.

Расход токенов возвращаем вызывающему (LLMResult.usage) — на нём построен
контроль затрат: services-слой пишет его в свою таблицу отчётов.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

import httpx

from app.config import get_settings

# Пишем в лог, но НЕ настраиваем его: настройка — дело точки входа
# (app/main.py), см. app/logging_setup.py.
logger = logging.getLogger(__name__)

BASE_URL = "https://api.deepseek.com"
POLZA_URL = "https://polza.ai/api/v1"

# deepseek-v4-flash — основной для отчётов: в разы дешевле pro на выходных
# токенах и держит 2500 параллельных запросов против 500. v4-pro можно
# передать параметром для платных отчётов, если глазами увидим разницу.
# (Старые deepseek-chat/deepseek-reasoner отключены 2026-07-24.)
DEFAULT_MODEL = "deepseek-v4-flash"

# Откалибровано на живых прогонах free_natal_v1 (scripts/report_cli.py,
# 2026-07-24): при 1.3 v4-flash к концу текста разваливается в бессвязный
# набор слов, при 1.0 (дефолт сервера) текст связный и в нужном тоне.
# В thinking-режиме temperature не отправляем вовсе: у размышляющих моделей
# DeepSeek параметр исторически игнорировался, для v4 поведение не описано.
DEFAULT_TEMPERATURE = 1.0

# Отчёт на 3–4 тыс. токенов при ~30 ток/с генерируется около двух минут —
# отсюда щедрый read-таймаут при обычном connect.
TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)

# Одна повторная попытка на сетевые сбои и перегрузку (429/5xx): чаще всего
# этого достаточно, а бесконечные ретраи на платном API опасны.
RETRY_STATUS = {429, 500, 502, 503, 504}
RETRY_DELAY_S = 3.0


class LLMError(RuntimeError):
    """Ошибка вызова LLM после всех повторов (сеть, статус, формат)."""


@dataclass(frozen=True, slots=True)
class Usage:
    prompt_tokens: int
    completion_tokens: int
    # Сколько токенов промпта отдано из кеша DeepSeek (их контекстный кеш
    # включён всегда и дешевле в ~10 раз) — метрика для оптимизации промптов:
    # стабильный системный префикс = больше cache hit.
    cached_prompt_tokens: int
    # Цена запроса в рублях — её считает только Polza; у DeepSeek None.
    cost_rub: float | None = None


@dataclass(frozen=True, slots=True)
class LLMResult:
    text: str
    model: str
    usage: Usage


def is_polza(model: str) -> bool:
    """Модели Polza пишутся как «провайдер/модель», у DeepSeek слэша нет."""
    return "/" in model


def _provider(model: str) -> str:
    return "Polza" if is_polza(model) else "DeepSeek"


_clients: dict[str, httpx.AsyncClient] = {}


def _client(model: str) -> httpx.AsyncClient:
    """Один AsyncClient на провайдера: переиспользуем TCP/TLS-соединения.

    Ключ читается здесь, а не на импорте — модуль можно импортировать
    без заполненного .env (тесты, alembic), см. app/config.py.
    Без POLZA_API_KEY модель со слэшем даёт LLMError.
    """
    provider = _provider(model)
    if provider not in _clients:
        settings = get_settings()
        if provider == "Polza":
            if settings.polza_api_key is None:
                raise LLMError("POLZA_API_KEY не задан в .env")
            base_url, key = POLZA_URL, settings.polza_api_key
        else:
            base_url, key = BASE_URL, settings.deepseek_api_key
        _clients[provider] = httpx.AsyncClient(
            base_url=base_url,
            timeout=TIMEOUT,
            headers={"Authorization": f"Bearer {key.get_secret_value()}"},
        )
    return _clients[provider]


def _reasoning(model: str, effort: str | None) -> dict:
    """Блок reasoning для Polza.

    Claude Opus 4.7+ и Sonnet 5.x думают в адаптивном режиме: Polza у них
    игнорирует reasoning.effort, глубину задаёт пара type=adaptive +
    effort_level (low/medium/high/max). Остальные модели — обычный effort.
    """
    out: dict = {"exclude": True}
    if effort is None:
        return out
    if model.startswith("anthropic/"):
        if effort in ("none", "minimal"):
            out["type"] = "disabled"
        else:
            out["type"] = "adaptive"
            out["effort_level"] = "max" if effort == "xhigh" else effort
    else:
        out["effort"] = effort
    return out


async def close_client() -> None:
    """Закрыть соединения на shutdown приложения (lifespan FastAPI)."""
    for client in _clients.values():
        await client.aclose()
    _clients.clear()


async def chat(
    system: str,
    user: str,
    *,
    model: str = DEFAULT_MODEL,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = 4096,
    thinking: bool = False,
    reasoning_effort: str | None = None,
) -> LLMResult:
    """Один запрос «system + user → текст ответа».

    Пары system/user достаточно для всех наших отчётов: диалогов с историей
    в продукте нет, каждый отчёт — независимая генерация.

    thinking=False потому, что в v4 режим рассуждений включён ПО УМОЛЧАНИЮ,
    а для развлекательных текстов он не улучшает результат, но умножает
    выходные токены и время ответа.

    reasoning_effort — только для Polza («none», «low», …); None — дефолт
    модели. У Claude и GPT размышления делят с текстом бюджет max_tokens.
    """
    payload: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "stream": False,
    }
    if is_polza(model):
        # Ход размышлений не возвращаем: в тексте разбора он не нужен, а платим за него и так.
        # temperature отправляем всегда: модели, которые её не принимают, Polza
        # выполняет без неё.
        payload["reasoning"] = _reasoning(model, reasoning_effort)
        payload["temperature"] = temperature
    else:
        payload["thinking"] = {"type": "enabled" if thinking else "disabled"}
        if not thinking:
            payload["temperature"] = temperature

    # В лог идут только размеры, не содержимое: в промпте лежит натальная
    # карта человека — дата, время и место рождения, то есть персональные
    # данные. Логи оседают в файлах и бэкапах, ПДн там быть не должно.
    logger.debug(
        "Запрос к %s: system=%d симв., user=%d симв., temperature=%s, thinking=%s",
        model,
        len(system),
        len(user),
        "n/a" if thinking else temperature,
        thinking,
    )
    started = time.perf_counter()

    last_error: Exception | None = None
    for attempt in (1, 2):
        try:
            resp = await _client(model).post("/chat/completions", json=payload)
        except httpx.HTTPError as e:
            last_error = e
            logger.warning(
                "Попытка %d/2: сеть недоступна (%s: %s)", attempt, type(e).__name__, e
            )
        else:
            if resp.status_code == 200:
                result = _parse(resp, requested_model=model)
                # Главная строка лога: по ней видно и скорость, и деньги.
                # cached — токены промпта, отданные из кеша DeepSeek: они в
                # ~10 раз дешевле, и по ним видно, работает ли стабильный
                # системный префикс промпта.
                logger.info(
                    "%s ответил за %.1f с: %d ток. промпт (%d из кеша) + %d ток. ответ",
                    result.model,
                    time.perf_counter() - started,
                    result.usage.prompt_tokens,
                    result.usage.cached_prompt_tokens,
                    result.usage.completion_tokens,
                )
                return result
            # Тело ошибки DeepSeek полезно в логах, но обрезаем: там может
            # быть эхо промпта.
            provider = _provider(model)
            last_error = LLMError(f"{provider} HTTP {resp.status_code}: {resp.text[:300]}")
            if resp.status_code not in RETRY_STATUS:
                logger.warning("%s вернул %d — не повторяем", provider, resp.status_code)
                break
            logger.warning(
                "Попытка %d/2: %s вернул %d, повтор через %.0f с",
                attempt,
                provider,
                resp.status_code,
                RETRY_DELAY_S,
            )
        if attempt == 1:
            await asyncio.sleep(RETRY_DELAY_S)

    # ERROR, а не просто исключение: вызывать нас будет фоновая задача, и без
    # этой строки причина провала не дойдёт ни до кого.
    logger.error(
        "%s не ответил за 2 попытки (%.1f с): %s",
        model,
        time.perf_counter() - started,
        last_error,
    )
    raise LLMError(f"{model} недоступен: {last_error}") from last_error


def _parse(resp: httpx.Response, *, requested_model: str) -> LLMResult:
    """Достаёт текст и расход токенов; неожиданный формат — тоже LLMError."""
    try:
        data = resp.json()
        choice = data["choices"][0]
        text = choice["message"]["content"]
        u = data["usage"]
        # Кеш промпта DeepSeek отдаёт своим полем, Polza — в формате OpenAI.
        cached = u.get("prompt_cache_hit_tokens")
        if cached is None:
            cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        usage = Usage(
            prompt_tokens=u["prompt_tokens"],
            completion_tokens=u["completion_tokens"],
            cached_prompt_tokens=cached,
            cost_rub=u.get("cost_rub", u.get("cost")),
        )
    except (KeyError, IndexError, ValueError, TypeError) as e:
        raise LLMError(f"Неожиданный формат ответа {requested_model}: {e}") from e

    # finish_reason == "length" значит, что текст обрезан по max_tokens:
    # для платного отчёта это брак — пусть вызывающий решает, что делать.
    if choice.get("finish_reason") == "length":
        # Отдельная строка про деньги: токены уже оплачены, а текст в брак.
        # Частое повторение = пора поднимать max_tokens или резать промпт.
        logger.warning(
            "Ответ обрезан по max_tokens: %d ток. сгенерировано и потрачено впустую",
            usage.completion_tokens,
        )
        raise LLMError(
            f"Ответ обрезан по max_tokens (сгенерировано {usage.completion_tokens} ток.)"
        )

    # Весь бюджет ушёл на размышления или провайдер отказал без ошибки — текста нет.
    if not text:
        raise LLMError(f"{requested_model} вернул пустой ответ ({choice.get('finish_reason')})")

    return LLMResult(text=text, model=data.get("model", requested_model), usage=usage)
