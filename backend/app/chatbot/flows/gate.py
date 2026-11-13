"""Вход только подписчикам: бесплатная КАРТА и ПАРА требуют подписки на сообщество.

Роутер зовёт closed() до первого вопроса сценария. Подписку спрашиваем
каждый раз заново и нигде не храним: отписался — следующий расчёт снова
попросит подписку. Сама проверка приходит снаружи функцией (у VK это
groups.isMember) — ядро про площадку не знает. VIP сюда не заходит.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.chatbot import states, storage, texts
from app.chatbot.types import Button, LinkButton, MemberCheck, Reply
from app.models import BotDialog, User
from app.services import events

TARGET_NATAL = "natal"
TARGET_PAIR = "pair"
CMD_CHECK = "gate_check"

_KEY = "gate"
_TEXTS = {TARGET_NATAL: texts.GATE_NATAL, TARGET_PAIR: texts.GATE_PAIR}
# Сообщение с датой рождения длиннее не бывает; простыню в data не кладём.
_TEXT_LIMIT = 500


def saved_text(dialog: BotDialog) -> str | None:
    """Сообщение с данными рождения, на котором человека остановил гейт."""
    mark = (dialog.data or {}).get(_KEY)
    text = mark.get("text") if isinstance(mark, dict) else None
    return text if isinstance(text, str) and text else None


async def closed(
    session: AsyncSession,
    user: User,
    dialog: BotDialog,
    check: MemberCheck | None,
    target: str,
    *,
    first_text: str | None = None,
    retry: bool = False,
) -> list[Reply] | None:
    """None — проход открыт; иначе ответ «подпишись» с двумя кнопками.

    retry — человек нажал «Я подписался»: при отказе текст другой и
    gate_shown второй раз не пишется. first_text сохраняется в data, чтобы
    после подписки продолжить с уже присланных данных.
    """
    if check is None:
        return None
    member = await check()
    data: dict[str, Any] = dict(dialog.data or {})
    if member is not False:
        # None — VK не ответил. Пропускаем: терять человека из-за сбоя VK дороже.
        if _KEY in data or (retry and member):
            data.pop(_KEY, None)
            await storage.save(session, dialog, data=data)
            if member:
                await events.log(
                    session, events.GATE_PASSED, user_id=user.id, target=target
                )
        return None

    # Чужой черновик не держим, но last оставляем: по нему VIP берёт данные.
    kept = {"last": data["last"]} if "last" in data else {}
    mark = {"target": target, "text": (first_text or "")[:_TEXT_LIMIT] or None}
    await storage.save(session, dialog, state=states.IDLE, data={**kept, _KEY: mark})
    if not retry:
        await events.log(session, events.GATE_SHOWN, user_id=user.id, target=target)
    return [
        Reply(
            text=texts.GATE_NOT_YET if retry else _TEXTS[target],
            buttons=[
                [LinkButton(texts.BTN_GATE_JOIN, texts.GROUP_URL)],
                [
                    Button(
                        texts.BTN_GATE_CHECK,
                        {"cmd": CMD_CHECK, "target": target},
                        color="positive",
                    )
                ],
            ],
            inline=True,
        )
    ]
