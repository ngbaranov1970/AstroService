"""Хендлеры бота: связывают команды и нажатия кнопок с сервисами и клавиатурами.

FSM не используется — весь сценарий идёт через инлайн-кнопки, а не через
свободный текст, поэтому "шаг" сценария всегда однозначно виден из того,
какая клавиатура прикреплена к сообщению. Постоянные данные (знак,
подписка) хранятся в БД, а не в состоянии диалога.
"""

from collections.abc import Awaitable

import aiosqlite
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from tg.keyboards import horoscope_keyboard, signs_keyboard
from tg.texts import (
    CHOOSE_PERIOD,
    CHOOSE_SIGN_FIRST,
    ERROR,
    GENERATING,
    HOROSCOPE,
    MESSAGE_EXPIRED,
    PERIODS,
    SIGNS,
    START,
    SUBSCRIBED,
    UNSUBSCRIBED,
)
from db import repo
from services.horoscope import HoroscopeService

router = Router()


async def _message_or_expired(callback: CallbackQuery) -> Message | None:
    """Message из коллбэка — или None, если сообщение слишком старое.

    Кнопки живут под сообщениями неделями (особенно под рассылкой), а для
    сообщений старше ~48 часов Telegram не присылает содержимое
    (InaccessibleMessage) и не позволяет их редактировать. В этом случае
    отвечаем алертом и просим начать заново — молча упасть нельзя,
    иначе у пользователя навсегда останутся «часики» на кнопке.
    """
    if isinstance(callback.message, Message):
        return callback.message
    await callback.answer(MESSAGE_EXPIRED, show_alert=True)
    return None


async def _edit(edit: Awaitable) -> None:
    """Выполнить edit_text/edit_reply_markup, пережив двойное нажатие кнопки.

    Повторное нажатие приводит к редактированию сообщения на точно такое же —
    Telegram отвечает ошибкой «message is not modified». Для нас это не сбой,
    а признак, что экран уже в нужном состоянии, поэтому гасим её;
    любые другие ошибки редактирования пробрасываем дальше.
    """
    try:
        await edit
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise


@router.message(CommandStart())
@router.message(Command("sign"))
async def cmd_start(message: Message) -> None:
    """/start и /sign ведут на один и тот же экран — выбор знака зодиака.

    Обе команды по смыслу — "начать выбор заново", поэтому не проверяем,
    есть ли уже сохранённый знак в БД: показываем клавиатуру безусловно.
    """
    await message.answer(START, reply_markup=signs_keyboard())


@router.callback_query(F.data == "menu:sign")
async def change_sign(callback: CallbackQuery) -> None:
    """Кнопка «Сменить знак» под гороскопом — тот же экран, что и /sign."""
    message = await _message_or_expired(callback)
    if message is None:
        return

    await _edit(message.edit_text(START, reply_markup=signs_keyboard()))
    await callback.answer()


@router.callback_query(F.data.startswith("sign:"))
async def choose_sign(callback: CallbackQuery, db: aiosqlite.Connection) -> None:
    """Пользователь выбрал знак — сохраняем и показываем выбор периода."""
    assert callback.data is not None  # гарантирован фильтром F.data.startswith
    message = await _message_or_expired(callback)
    if message is None:
        return

    sign = callback.data.split(":", 1)[1]
    await repo.upsert_user(db, callback.from_user.id, sign)

    sign_name = SIGNS[sign][0]
    subscribed = await repo.is_subscribed(db, callback.from_user.id)
    await _edit(message.edit_text(
        CHOOSE_PERIOD.format(sign=sign_name),
        reply_markup=horoscope_keyboard(current_period="", subscribed=subscribed),
    ))
    await callback.answer()


@router.callback_query(F.data.startswith("period:"))
async def choose_period(
    callback: CallbackQuery,
    db: aiosqlite.Connection,
    horoscope_service: HoroscopeService,
) -> None:
    """Пользователь выбрал период — показываем гороскоп для его знака."""
    assert callback.data is not None  # гарантирован фильтром F.data.startswith
    message = await _message_or_expired(callback)
    if message is None:
        return

    period = callback.data.split(":", 1)[1]
    sign = await repo.get_user_sign(db, callback.from_user.id)
    if sign is None:
        # Кнопка периода видна только после выбора знака, но запись могла
        # пропасть из БД (например, база не переехала вместе с ботом на
        # новый сервер) — тогда возвращаем пользователя к выбору знака.
        await _edit(message.edit_text(CHOOSE_SIGN_FIRST, reply_markup=signs_keyboard()))
        await callback.answer()
        return

    # Промежуточное сообщение: запрос к DeepSeek занимает секунды.
    await _edit(message.edit_text(GENERATING))
    await callback.answer()

    try:
        text = await horoscope_service.get(sign, period)
    except Exception:
        await _edit(message.edit_text(ERROR))
        return

    sign_name, emoji, _ = SIGNS[sign]
    subscribed = await repo.is_subscribed(db, callback.from_user.id)
    await _edit(message.edit_text(
        HOROSCOPE.format(emoji=emoji, sign=sign_name, period_label=PERIODS[period], text=text),
        reply_markup=horoscope_keyboard(current_period=period, subscribed=subscribed),
    ))


@router.callback_query(F.data.startswith("sub:"))
async def toggle_subscription(callback: CallbackQuery, db: aiosqlite.Connection) -> None:
    """Кнопка подписки под гороскопом. Период приходит третьим сегментом
    callback_data (см. bot/keyboards.py) — так хендлер знает, какую
    клавиатуру перерисовать, не храня "текущий экран" отдельно.
    """
    assert callback.data is not None  # гарантирован фильтром F.data.startswith
    message = await _message_or_expired(callback)
    if message is None:
        return

    _, action, period = callback.data.split(":", 2)
    enabled = action == "on"
    await repo.set_subscription(db, callback.from_user.id, enabled)

    await _edit(message.edit_reply_markup(
        reply_markup=horoscope_keyboard(current_period=period, subscribed=enabled)
    ))
    await callback.answer(SUBSCRIBED if enabled else UNSUBSCRIBED)


@router.message(Command("sub"))
async def cmd_sub(message: Message, db: aiosqlite.Connection) -> None:
    """/sub переключает подписку независимо от того, какой экран открыт у пользователя."""
    assert message.from_user is not None  # команда пришла в личном чате, не от имени канала

    sign = await repo.get_user_sign(db, message.from_user.id)
    if sign is None:
        await message.answer(CHOOSE_SIGN_FIRST, reply_markup=signs_keyboard())
        return

    enabled = not await repo.is_subscribed(db, message.from_user.id)
    await repo.set_subscription(db, message.from_user.id, enabled)
    await message.answer(SUBSCRIBED if enabled else UNSUBSCRIBED)
