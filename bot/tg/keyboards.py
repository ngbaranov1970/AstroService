"""Инлайн-клавиатуры бота.

Коллбэки везде в формате "неймспейс:ключ" (например "sign:aries",
"period:week", "sub:on") — хендлеры разбирают callback_data через
split(":", 1) и сразу знают, к какому разделу относится нажатие.
"""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from tg.texts import PERIODS, SIGNS, sign_button_label


def signs_keyboard() -> InlineKeyboardMarkup:
    """Выбор знака зодиака: по 3 кнопки в ряд.

    Используется и при первом входе, и при смене знака (/sign) —
    это одна и та же клавиатура.
    """
    builder = InlineKeyboardBuilder()
    for key in SIGNS:
        builder.button(text=sign_button_label(key), callback_data=f"sign:{key}")
    builder.adjust(3)
    return builder.as_markup()


def horoscope_keyboard(current_period: str, subscribed: bool) -> InlineKeyboardMarkup:
    """Клавиатура под текстом гороскопа: смена периода + подписка.

    Кнопка текущего периода помечена ✅ — это просто индикатор,
    от повторного запроса на тот же период защищает хендлер, а не клавиатура.
    """
    builder = InlineKeyboardBuilder()
    for key, label in PERIODS.items():
        text = f"✅ {label}" if key == current_period else label
        builder.button(text=text, callback_data=f"period:{key}")

    # Период зашит третьим сегментом callback_data, чтобы хендлер знал,
    # какую клавиатуру перерисовать после переключения — без этого
    # пришлось бы отдельно хранить "текущий экран" пользователя.
    sub_text = "🔕 Отписаться" if subscribed else "🔔 Подписаться"
    sub_action = "off" if subscribed else "on"
    builder.button(text=sub_text, callback_data=f"sub:{sub_action}:{current_period}")
    builder.button(text="🔄 Сменить знак", callback_data="menu:sign")

    builder.adjust(4, 2)
    return builder.as_markup()
