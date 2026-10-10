"""Текст VIP-разбора: черновик, правки владельца и доставка в личку.

Место в цепочке: web/routes/admin.py → ЭТОТ МОДУЛЬ → vip (заказ с картой),
llm (vip_natal, vip_edit), vip_check, vk. Деньги и статусы оплаты здесь
только читаются. Черновик живёт в orders.draft_text, вариант от DeepSeek —
в draft_candidate до «принять / отказаться» (docs/db-schema.md). Модель
выбирают на карточке из vip_natal.MODELS.
Транзакцию функции не коммитят.
"""

from __future__ import annotations

import logging
import zlib
from datetime import UTC, datetime
from types import ModuleType

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.llm import client as llm
from app.llm.prompts import vip_edit, vip_natal
from app.services import vip, vip_check
from app.services.vip import VipOrder
from app.vk import api as vk_api

logger = logging.getLogger(__name__)

# Предел messages.send — 4096 символов; запас на случай, если VK считает иначе.
MESSAGE_LIMIT = 4000

# Разделители, которые пишет vip_natal.to_draft.
BLOCK_SEP = "\n\n\n"
PARAGRAPH_SEP = "\n\n"


class VipDraftError(RuntimeError):
    """Действие с текстом невозможно; сообщение — для показа в админке."""


async def _load(session: AsyncSession, order_id: int) -> VipOrder:
    """Заказ, текст которого ещё можно менять: найден, есть карта, не отправлен."""
    item = await vip.get_order(session, order_id)
    if item is None:
        raise VipDraftError(f"VIP-заказа №{order_id} нет")
    if item.order.delivered_at is not None:
        raise VipDraftError("Разбор уже отправлен — текст менять нельзя")
    if item.chart is None:
        raise VipDraftError("Карта заказа удалена — черновик строить не по чему")
    return item


def _model(model: str | None) -> str:
    """Модель из формы карточки; пусто — по умолчанию, чужое имя — ошибка."""
    if not model:
        return vip_natal.MODEL
    # Имя приходит из POST: без проверки форма могла бы заказать любую модель OpenRouter.
    if model not in vip_natal.MODELS:
        raise VipDraftError(f"Модели {model} нет в списке")
    return model


async def _generate(prompt: ModuleType, user_prompt: str, order_id: int, model: str) -> str:
    """Один вызов модели по модулю промпта → текст в формате draft_text."""
    try:
        result = await llm.chat(
            prompt.SYSTEM,
            user_prompt,
            model=model,
            temperature=prompt.TEMPERATURE,
            max_tokens=prompt.MAX_TOKENS,
            reasoning_effort=prompt.REASONING_EFFORT,
        )
    except llm.LLMError as e:
        logger.error("Заказ %s: %s на %s не ответил: %s", order_id, prompt.VERSION, model, e)
        raise VipDraftError(f"{vip_natal.MODELS[model]} не ответил — попробуй ещё раз") from e

    sections = prompt.parse(result.text)
    logger.info(
        "Заказ %s: %s на %s, ответ %d симв., %d ток.",
        order_id, prompt.VERSION, model, len(result.text), result.usage.completion_tokens,
    )
    if not sections:
        raise VipDraftError("Ответ модели не разобрался (оборван или не JSON) — попробуй ещё раз")
    return prompt.to_draft(sections)


async def make_draft(session: AsyncSession, order_id: int, model: str | None = None) -> VipOrder:
    """Черновик от vip_natal — в любом статусе оплаты.

    Первый черновик сразу встаёт в draft_text; повторный — в draft_candidate,
    чтобы не затереть уже вычитанный текст. Вызов модели идёт до двух минут.
    """
    model = _model(model)
    item = await _load(session, order_id)
    chart, order = item.chart, item.order
    assert chart is not None  # проверено в _load
    user_prompt = vip_natal.build_user(
        chart.calc["natal"], order.client_gender, time_known=chart.time_known
    )
    text = await _generate(vip_natal, user_prompt, order_id, model)
    if order.draft_text is None:
        order.draft_text = text
    else:
        order.draft_candidate = text
    return item


async def request_edit(
    session: AsyncSession, order_id: int, request: str, model: str | None = None
) -> VipOrder:
    """Правка просьбой к модели: результат в draft_candidate, draft_text не трогаем.

    Применяется к текущему draft_text; прежний непринятый вариант заменяется.
    """
    model = _model(model)
    request = request.strip()
    if not request:
        raise VipDraftError("Просьба пустая")
    item = await _load(session, order_id)
    chart, order = item.chart, item.order
    assert chart is not None
    if not order.draft_text:
        raise VipDraftError("Черновика ещё нет — сначала «Сделать черновик»")
    user_prompt = vip_edit.build_user(
        chart.calc["natal"],
        order.client_gender,
        draft=order.draft_text,
        request=request,
        time_known=chart.time_known,
    )
    candidate = await _generate(vip_edit, user_prompt, order_id, model)
    # Вариант слово в слово равен черновику — не записываем: иначе на карточке
    # две одинаковые колонки. Так модель отвечает на просьбы «короче вдвое».
    if candidate.strip() == order.draft_text.strip():
        raise VipDraftError("Модель ничего не изменила — переформулируй просьбу")
    order.draft_candidate = candidate
    return item


async def accept_candidate(session: AsyncSession, order_id: int) -> VipOrder:
    """Новый вариант становится черновиком."""
    item = await _load(session, order_id)
    order = item.order
    if order.draft_candidate is None:
        raise VipDraftError("Нового варианта нет")
    order.draft_text, order.draft_candidate = order.draft_candidate, None
    return item


async def reject_candidate(session: AsyncSession, order_id: int) -> VipOrder:
    """Отказ от варианта: черновик остаётся прежним."""
    item = await _load(session, order_id)
    item.order.draft_candidate = None
    return item


async def save_draft(session: AsyncSession, order_id: int, text: str) -> VipOrder:
    """Ручная правка черновика. Непринятый вариант не трогает."""
    # textarea присылает \r\n — без замены нарезка по пустым строкам не сработает.
    text = text.replace("\r\n", "\n").strip()
    if not text:
        raise VipDraftError("Пустой текст не сохраняю")
    item = await _load(session, order_id)
    item.order.draft_text = text
    return item


def check_draft(item: VipOrder, text: str | None = None) -> list[vip_check.Finding]:
    """Подозрительные места текста (по умолчанию draft_text) по расчёту карты заказа."""
    text = item.order.draft_text if text is None else text
    if not text or item.chart is None:
        return []
    return vip_check.check(text, item.chart.calc["natal"], time_known=item.chart.time_known)


# --- Доставка ----------------------------------------------------------------


def header(item: VipOrder) -> str:
    """«Твоя натальная карта: 14.07.1988, 10:20, Судак» — собирает код, не модель."""
    chart = item.chart
    assert chart is not None
    when = f"{chart.birth_local:%d.%m.%Y}, " + (
        f"{chart.birth_local:%H:%M}" if chart.time_known else "время неизвестно"
    )
    return f"Твоя натальная карта: {when}, {chart.place_name}"


def split_message(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """Текст → части не длиннее limit: целыми блоками, затем абзацами, затем по словам."""
    parts: list[str] = []
    for unit, sep in _units(text, limit):
        if parts and len(parts[-1]) + len(sep) + len(unit) <= limit:
            parts[-1] += sep + unit
        else:
            parts.append(unit)
    return parts


def _units(text: str, limit: int) -> list[tuple[str, str]]:
    """Куски не длиннее limit и разделитель, которым кусок пристыкуется к предыдущему."""
    out: list[tuple[str, str]] = []
    for block in text.strip().split(BLOCK_SEP):
        if len(block) <= limit:
            out.append((block, BLOCK_SEP))
            continue
        # Блок длиннее сообщения: заголовок едет с первым абзацем, дальше по абзацам.
        sep = BLOCK_SEP
        paras = block.split(PARAGRAPH_SEP)
        # Склейка до нарезки: иначе при длинном первом абзаце заголовок уходит один.
        if len(paras) > 1:
            paras[:2] = [paras[0] + PARAGRAPH_SEP + paras[1]]
        for para in paras:
            while len(para) > limit:
                cut = para.rfind(" ", 0, limit)
                cut = cut if cut > 0 else limit
                out.append((para[:cut], sep))
                para, sep = para[cut:].lstrip(), " "
            out.append((para, sep))
            sep = PARAGRAPH_SEP
    return out


def _part_random_id(order_id: int, index: int, part: str) -> int:
    """random_id части: повтор того же текста в течение часа VK отбросит сам.

    Текст части входит в ключ: исправленная после сбоя часть уйдёт заново.
    """
    return zlib.crc32(f"vip-deliver:{order_id}:{index}:{part}".encode()) & 0x7FFFFFFF


async def deliver(session: AsyncSession, order_id: int) -> VipOrder:
    """Отправить разбор в личку клиенту частями и проставить delivered_at.

    Только по оплаченному заказу, с черновиком и без непринятого варианта.
    Оборвалась отправка — delivered_at не ставится, VipDraftError говорит,
    сколько частей ушло; повтор уже ушедшие VK отбросит по random_id.
    """
    item = await _load(session, order_id)
    order = item.order
    if not order.is_paid:
        raise VipDraftError("Заказ не оплачен — отправлять нельзя")
    if not order.draft_text:
        raise VipDraftError("Черновика нет")
    if order.draft_candidate is not None:
        raise VipDraftError("Есть непринятый вариант — прими его или откажись")
    if not get_settings().vk_messaging_enabled:
        raise VipDraftError("Писать нечем: нет ключа сообщества или VK_GROUP_ID")

    text = f"{header(item)}{BLOCK_SEP}{order.draft_text.strip()}"
    parts = split_message(text)
    peer = int(item.user.external_id)
    for i, part in enumerate(parts):
        try:
            [result] = await vk_api.send_messages(
                [peer], part, random_id=_part_random_id(order_id, i, part)
            )
        except (vk_api.VkError, ValueError) as e:
            logger.error("Заказ %s: часть %d/%d не ушла: %s", order_id, i + 1, len(parts), e)
            raise VipDraftError(f"Ушло {i} из {len(parts)} частей: VK не ответил") from e
        if not result.delivered:
            logger.error(
                "Заказ %s: часть %d/%d не ушла, VK %s", order_id, i + 1, len(parts), result.error_code
            )
            raise VipDraftError(f"Ушло {i} из {len(parts)} частей: VK {result.error_code}")

    # В draft_text остаётся ровно отправленное — вместе с шапкой (docs/db-schema.md).
    order.draft_text = text
    order.delivered_at = datetime.now(UTC)
    logger.info("Заказ %s: разбор отправлен, %d частей, %d симв.", order_id, len(parts), len(text))
    return item
