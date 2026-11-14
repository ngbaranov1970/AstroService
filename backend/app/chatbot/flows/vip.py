"""VIP-разбор из бота: описание → данные → имя и род → заказ и ссылка на оплату.

Заказ заводит тот же services/vip.create_order, что и админка; после оплаты
всё как прежде — вебхук ЮKassa, notify_paid, черновик и отправка владельцем.
Оплата только по внешней ссылке на сайт (CLAUDE.md, «Жёсткие ограничения»).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.chatbot import states, storage, texts
from app.chatbot.flows import natal
from app.chatbot.types import Button, Incoming, LinkButton, Outgoing, Reply
from app.models import BotDialog, ClientGender, OrderStatus, ReportType, User
from app.services import events, orders
from app.services import vip as vip_service

logger = logging.getLogger(__name__)

_NAME_MAX = 60
# «Меня зовут Анна», «я Анна», «Анна.» → «Анна».
_NAME_PREFIX = re.compile(r"^(?:меня\s+зовут|зовут|я|имя)[\s:,-]+", re.IGNORECASE)
_GENDERS = {
    ClientGender.FEMALE.value: texts.GENDER_FEMALE,
    ClientGender.MALE.value: texts.GENDER_MALE,
}


def _price() -> int:
    return orders.price_rub(ReportType.VIP_NATAL)


async def show(session: AsyncSession, user: User) -> list[Outgoing]:
    """Описание VIP и кнопка «Оформить»."""
    if not orders.is_for_sale(ReportType.VIP_NATAL):
        return [Reply(text=texts.VIP_NOT_FOR_SALE)]
    await events.log(session, events.BOT_VIP_SHOWN, user_id=user.id)
    price = _price()
    rows = [[Button(texts.BTN_VIP_GO.format(price=price), {"cmd": "vip_go"}, color="positive")]]
    if orders.is_for_sale(ReportType.VIP_PAIR):
        # cmd "pair_vip" обрабатывает flows/pair.py: пара без тизера и без гейта подписки.
        pair_price = orders.price_rub(ReportType.VIP_PAIR)
        rows.append([Button(texts.BTN_VIP_PAIR.format(price=pair_price), {"cmd": "pair_vip"})])
    rows.append([Button(texts.BTN_MENU, {"cmd": "menu"})])
    return [
        Reply(
            text=texts.VIP_INTRO.format(price=price),
            buttons=rows,
            inline=True,
        )
    ]


async def dispatch(
    session: AsyncSession,
    user: User,
    dialog: BotDialog,
    payload: dict[str, Any],
) -> list[Outgoing] | None:
    """Кнопки VIP; None — команда не отсюда."""
    cmd = payload.get("cmd")
    data = dict(dialog.data or {})
    if cmd == "vip":
        return await show(session, user)
    if cmd == "vip_go":
        return await _go(session, user, dialog, data)
    if cmd == "vip_skip_name" and dialog.state == states.VIP_NAME:
        data["name"] = None
        return await _ask_gender(session, dialog, data)
    if cmd == "vip_gender" and payload.get("g") in _GENDERS:
        data["gender"] = payload["g"]
        return await _confirm(session, dialog, data)
    if cmd == "vip_fix":
        return await natal.start(session, user, dialog, purpose=natal.PURPOSE_VIP)
    if cmd == "vip_confirm" and dialog.state == states.VIP_CONFIRM:
        return await _create(session, user, dialog, data)
    return None


async def handle_text(
    session: AsyncSession,
    user: User,
    dialog: BotDialog,
    incoming: Incoming,
) -> list[Outgoing]:
    """Текст в состояниях vip_*: имя; на кнопочных шагах — повторить вопрос."""
    data = dict(dialog.data or {})
    if dialog.state == states.VIP_NAME:
        name = clean_name(incoming.text)
        if not name:
            return await after_birth_data(session, user, dialog, data)
        data["name"] = name
        return await _ask_gender(session, dialog, data)
    if dialog.state == states.VIP_GENDER:
        return await _ask_gender(session, dialog, data)
    return await _confirm(session, dialog, data)


async def after_birth_data(
    session: AsyncSession,
    user: User,
    dialog: BotDialog,
    data: dict[str, Any],
) -> list[Outgoing]:
    """Данные рождения собраны — спросить имя (natal зовёт при purpose=vip)."""
    await storage.save(session, dialog, state=states.VIP_NAME, data=data)
    return [
        Reply(
            text=texts.VIP_ASK_NAME,
            buttons=[[Button(texts.BTN_SKIP, {"cmd": "vip_skip_name"})]],
            inline=True,
        )
    ]


async def _go(
    session: AsyncSession,
    user: User,
    dialog: BotDialog,
    data: dict[str, Any],
) -> list[Outgoing]:
    last = data.get("last")
    if isinstance(last, dict) and {"date", "time_known", "place"} <= last.keys():
        draft = {"purpose": natal.PURPOSE_VIP, **last}
        same = Reply(text=texts.VIP_SAME_DATA.format(summary=natal.summary(draft)))
        return [same, *(await after_birth_data(session, user, dialog, draft))]
    return await natal.start(session, user, dialog, purpose=natal.PURPOSE_VIP)


async def _ask_gender(
    session: AsyncSession, dialog: BotDialog, data: dict[str, Any]
) -> list[Outgoing]:
    await storage.save(session, dialog, state=states.VIP_GENDER, data=data)
    return [
        Reply(
            text=texts.VIP_ASK_GENDER,
            buttons=[
                [
                    Button(texts.BTN_FEMALE, {"cmd": "vip_gender", "g": ClientGender.FEMALE.value}),
                    Button(texts.BTN_MALE, {"cmd": "vip_gender", "g": ClientGender.MALE.value}),
                ]
            ],
            inline=True,
        )
    ]


async def _confirm(
    session: AsyncSession, dialog: BotDialog, data: dict[str, Any]
) -> list[Outgoing]:
    await storage.save(session, dialog, state=states.VIP_CONFIRM, data=data)
    text = texts.VIP_CONFIRM.format(
        summary=natal.summary(data),
        name=data.get("name") or texts.VIP_NO_NAME,
        gender=_GENDERS.get(data.get("gender") or "", "—"),
    )
    return [
        Reply(
            text=text,
            buttons=[
                [
                    Button(texts.BTN_CONFIRM, {"cmd": "vip_confirm"}, color="positive"),
                    Button(texts.BTN_FIX, {"cmd": "vip_fix"}),
                ]
            ],
            inline=True,
        )
    ]


async def _create(
    session: AsyncSession,
    user: User,
    dialog: BotDialog,
    data: dict[str, Any],
) -> list[Outgoing]:
    birth_local, time_known, place = natal.birth_values(data)
    try:
        order = await vip_service.create_order(
            session,
            vk_user_id=int(user.external_id),
            birth_local=birth_local,
            time_known=time_known,
            place=place,
            client_gender=ClientGender(data["gender"]),
            person_name=data.get("name"),
        )
    except orders.NotForSaleError:
        await storage.reset(session, dialog)
        return [Reply(text=texts.VIP_NOT_FOR_SALE)]

    # Данные рождения оставляем под last: повторный заказ не спросит их снова.
    await storage.save(
        session, dialog, state=states.IDLE, data={"last": natal.birth_only(data)}
    )
    if order.status is OrderStatus.PAID:
        # create_order отдаёт прежний заказ на ту же карту — второй VIP не заводится.
        logger.info("chatbot.vip already paid user_id=%s order_id=%s", user.id, order.id)
        return [Reply(text=texts.VIP_ALREADY_PAID)]
    url = vip_service.payment_url(order)
    if url is None:
        # Владелец увидит заказ в /admin/vip и напишет сам; в лог — только id.
        logger.warning(
            "chatbot.vip payment unavailable user_id=%s order_id=%s", user.id, order.id
        )
        return [Reply(text=texts.VIP_PAY_UNAVAILABLE)]
    return [
        Reply(
            text=texts.VIP_PAY,
            buttons=[[LinkButton(texts.BTN_PAY.format(price=_price()), url)]],
            inline=True,
        )
    ]


def clean_name(text: str) -> str:
    """«Меня зовут Анна.» → «Анна». Им же чистит имя партнёра flows/pair.py."""
    name = _NAME_PREFIX.sub("", text.strip()).strip(" .,!")
    return name[:_NAME_MAX]
