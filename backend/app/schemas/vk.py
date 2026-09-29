"""Схема состояния человека в сообществе VK: подписка и разрешение писать.

Граница с app/models/: там строка `users` со всем внутренним, здесь — только
то, что нужно фронту, чтобы решить вид двух кнопок.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from pydantic import BaseModel, field_validator

from app.schemas.charts import MIN_BIRTH_YEAR


class GroupStateOut(BaseModel):
    """Состояние в сообществе: подписан ли человек и разрешил ли писать."""

    # None — выяснить не удалось: нет VK_GROUP_ID, VK не ответил или запретил
    # метод ключу сообщества. Фронт на None показывает кнопку «Подписаться»,
    # как показывал бы неподписанному: предложить лишний раз дешевле, чем
    # спрятать блок у того, кто на самом деле не подписан.
    joined: bool | None
    # Разрешение канала: сообществу позволено писать этому человеку. Наш
    # собственный флаг, он известен точно: VK список разрешивших не отдаёт.
    # Даётся один раз окном Bridge и живёт до отзыва в самом VK.
    messages_allowed: bool
    # Решение о письмах, отдельное от разрешения канала: выключил рассылку —
    # разрешение осталось. Благодаря этому кнопку «присылать» можно вернуть
    # без окна VK, то есть и вне Mini App. Письма идут, когда оба поля true.
    subscribed: bool
    # Числовой id сообщества из VK_GROUP_ID — без него фронт не может позвать
    # VKWebAppJoinGroup и VKWebAppAllowMessagesFromGroup. Отдаём отсюда, а не
    # переменной сборки: второй экземпляр числа однажды разъехался бы с тем,
    # по которому считается joined. None — сообщество не настроено, блока в
    # приложении нет вовсе.
    group_id: int | None = None
    # Знак, введённый одной датой (users.sun_sign). Экран гороскопа по нему
    # решает, спрашивать ли дату. Знак из карты сюда не кладём: у кого карта
    # есть, тому дату и так не показывают.
    sun_sign: str | None = None


class SunSignIn(BaseModel):
    """Дата рождения с короткого пути к рассылке — без времени и места."""

    birth_date: date

    @field_validator("birth_date")
    @classmethod
    def _sane_birth_date(cls, value: date) -> date:
        # Те же границы, что у даты карты (schemas/charts.py): расходиться
        # им незачем, человек потом введёт ту же дату в онбординге.
        if value.year < MIN_BIRTH_YEAR:
            raise ValueError(f"Даты раньше {MIN_BIRTH_YEAR} года не поддерживаются")
        if value > datetime.now(UTC).date():
            raise ValueError("Дата рождения в будущем")
        return value


class SunSignOut(BaseModel):
    """Знак по дате. None — день смены знака: нужен полный онбординг."""

    sign: str | None
