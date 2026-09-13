"""Схемы слоя пар: сборка пары на вход, пара и приглашение на выход.

Та же роль, что у app/schemas/charts.py, — граница HTTP и отбор полей
наружу. Ни SQL, ни расчёта здесь нет.

Наружу из app/models/pair.py уходит не всё. Адресом пары служит публичный
`token`, внутренние `id` и `user_id` не отдаются, а `invite_token`
показывается ровно один раз — тому, кто приглашение создал.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import PairKind

# Столько же, сколько в колонке pairs.inviter_label.
MAX_LABEL = 64


class PairIn(BaseModel):
    """Сборка пары: две карты и тип связи.

    Карты адресуются токенами, а не id: наружу id не уходит нигде, и в
    списке карт фронт получает именно token.
    """

    chart_a_token: str
    # None означает «партнёра позову ссылкой»: пара создаётся с пустой
    # стороной B и ждёт. Отдельного маршрута под приглашение не нужно —
    # это то же самое действие, просто вторая карта приедет позже.
    chart_b_token: str | None = None
    kind: PairKind
    # Как назвать инициатора партнёру на анонимном экране. Отдельное поле, а
    # не имя карты: имя карты человек вводил про себя и для себя.
    inviter_label: str | None = Field(default=None, max_length=MAX_LABEL)

    @field_validator("inviter_label")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        """Пустая строка — это «не подписался», то есть NULL в базе."""
        if value is None:
            return None
        return value.strip() or None


class PairOut(BaseModel):
    """Пара в списке: с кем она, какого рода и собрана ли.

    Подписи сторон и `invite_open` заполняет маршрут: первые лежат в
    соседних картах, а вторая считается от текущего времени.
    """

    model_config = ConfigDict(from_attributes=True)

    token: str
    kind: PairKind
    # False означает «ждём партнёра по ссылке»: расчёта и текстов ещё нет.
    is_complete: bool
    created_at: datetime

    # Имена обеих карт — обе принадлежат инициатору, и список без них
    # читается как «пара, пара, пара». None — карта не подписана.
    person_a: str | None = None
    person_b: str | None = None

    # Можно ли сейчас войти по приглашению этой пары. Сам токен не отдаём:
    # он по смыслу пароль и живёт только в ссылке у инициатора.
    invite_open: bool = False


class PairDetail(PairOut):
    """Пара вместе с расчётом синастрии — для экрана пары.

    `calc` отдаётся целиком тем же словарём, что лежит в базе (meta с
    таблицей орбов, aspects, summary). None, пока партнёр не пришёл.
    """

    calc: dict | None = None


class InviteOut(BaseModel):
    """Выданное приглашение — единственное место, где токен уходит наружу.

    Адрес ссылки собирает фронт: он один знает, на каком домене открыт и
    какой у экрана приглашения путь.
    """

    invite_token: str
    expires_at: datetime


class InvitePublic(BaseModel):
    """Что видит по ссылке человек без авторизации.

    Минимум по составу: кто зовёт и на какой разбор. Ни дат, ни мест
    рождения, ни токена пары — партнёр согласия на чужие данные не давал, а
    по этим двум полям он решает, вводить ли свои.

    None в `inviter_label` — инициатор не подписался, экран скажет
    безличное «вас приглашают».
    """

    kind: PairKind
    inviter_label: str | None
