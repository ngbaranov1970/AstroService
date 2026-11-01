"""Имена состояний FSM диалога — строки в bot_dialogs.state.

Значения совпадают с тем, что лежит в БД: отдельный enum не нужен
(как у PostKind до CHECK), а опечатку поймает тест/ревью.
"""

from __future__ import annotations

IDLE = "idle"
ASK_SIGN = "ask_sign"
NATAL_DATE = "natal_date"
NATAL_TIME = "natal_time"
NATAL_CITY = "natal_city"
NATAL_CITY_PICK = "natal_city_pick"
NATAL_WAIT = "natal_wait"
VIP_NAME = "vip_name"
VIP_GENDER = "vip_gender"
VIP_CONFIRM = "vip_confirm"
