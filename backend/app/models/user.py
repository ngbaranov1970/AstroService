"""Пользователь: одна учётная запись на одной платформе.

Голова графа: карты и заказы ссылаются сюда, отсюда же тянутся правила
удаления данных. Общие соглашения пакета — в app/models/__init__.py.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

# Только для проверки типов: в рантайме эти импорты создали бы цикл
# (charts.py и orders.py сами импортируют User). Имена в аннотациях
# relationship SQLAlchemy разрешает через свой реестр классов, а не через
# модуль, — поэтому связям такого импорта достаточно.
if TYPE_CHECKING:
    from app.models.chart import Chart
    from app.models.order import Order


class Platform(str, enum.Enum):
    """Откуда пришёл пользователь.

    str-миксин нужен, чтобы значение можно было сравнивать со строкой и
    без потерь класть в JSON: Platform.TG == "tg" истинно.

    В базе это НЕ нативный PG-тип ENUM, а varchar с CHECK-ограничением
    (см. native_enum=False ниже). Причина практическая: добавление значения
    в нативный enum — это ALTER TYPE, который Alembic не умеет генерировать
    автоматически и который в старых версиях Postgres нельзя выполнить
    внутри транзакции миграции. CHECK правится обычным DDL.
    """

    TG = "tg"
    VK = "vk"
    WEB = "web"


class User(Base):
    """Пользователь — одна учётная запись на одной платформе.

    Регистрации в сервисе нет: пользователь появляется в момент первого
    касания (написал боту / открыл Mini App) и опознаётся парой
    «платформа + идентификатор в ней».

    Один и тот же человек в Telegram и во ВКонтакте — это ДВЕ строки.
    Связать их можно только по добровольному действию (ввёл один и тот же
    код в обоих каналах); до появления такой функции честнее считать их
    разными пользователями, чем угадывать по совпадению имени.

    Чего здесь намеренно нет:

    * счётчика бесплатных разборов. Продуктов несколько (натальная карта,
      Матрица Судьбы), бесплатная приманка — у каждого своя, и одно число
      их не различает. Лимит считается запросом к reports по (user_id, type),
      а пороги живут в конфиге: их правка не должна требовать миграции;

    * признака администратора. Администраторов полтора человека, их список
      в .env дешевле колонки, которую надо чем-то заполнять в проде.
    """

    __tablename__ = "users"

    __table_args__ = (
        # Ключевое ограничение таблицы: уникальна ПАРА, а не external_id сам
        # по себе. Идентификатор 12345 в Telegram и 12345 во ВКонтакте —
        # разные люди, и без платформы в ключе они схлопнулись бы в одного.
        UniqueConstraint("platform", "external_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # --- Идентичность ----------------------------------------------------
    platform: Mapped[Platform] = mapped_column(
        SAEnum(
            Platform,
            name="platform",
            native_enum=False,  # varchar + CHECK вместо нативного PG ENUM
            create_constraint=True,  # в SQLAlchemy 2 CHECK по умолчанию НЕ создаётся
            length=8,
            # Без values_callable SQLAlchemy пишет в базу ИМЕНА членов
            # перечисления ("TG"), а не значения ("tg"). В базу должно
            # попадать то же, что уходит в JSON и в логи.
            values_callable=lambda cls: [m.value for m in cls],
        ),
        nullable=False,
    )

    # Строка, хотя и в Telegram, и во ВКонтакте это числа: веб-пользователь
    # придёт с идентификатором другой природы, а менять тип колонки на живой
    # базе дороже, чем сразу писать varchar.
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)

    # --- Профиль ---------------------------------------------------------
    # Имя, которое человек ввёл САМ. Идёт в текст разбора и в обращение.
    name: Mapped[str | None] = mapped_column(String(64))

    # @username в Telegram / имя аккаунта во ВКонтакте — только для поддержки
    # и админки. Отдельно от name намеренно: человек переименовался в мессен-
    # джере — это не повод менять имя в его натальной карте, и наоборот.
    platform_username: Mapped[str | None] = mapped_column(String(64))

    # Метка канала привлечения, проставляется ОДИН раз при первом касании:
    # payload из deep-link Telegram (t.me/bot?start=vk_post_12), vk_ref или
    # utm из launch params, utm с сайта. Значения придумываем сами
    # ("vk_ads_may", "bot_old_base"). NULL — «не знаем», это честнее, чем
    # записывать всех безымянных в "direct".
    #
    # Живёт в users, а не только в events, потому что канал — неизменное
    # свойство пользователя, и отчёт «какой канал приводит платящих» должен
    # быть джойном с orders, а не разбором JSONB в таблице-потоке.
    source: Mapped[str | None] = mapped_column(String(64))

    # Карта САМОГО пользователя среди его карт. Нужна рассылке: гороскоп
    # шлётся по знаку Солнца, а карт у человека бывает несколько — своя,
    # мужа, подруги, — и угадывать «наверное, первая» значит однажды слать
    # человеку чужой знак каждый день.
    #
    # Внешний ключ смотрит на charts, у которой ключ смотрит сюда: цикл в
    # схеме допустим, потому что колонка nullable. Связи relationship
    # намеренно нет — она заставила бы SQLAlchemy разрывать цикл через
    # post_update, добавляя лишний UPDATE к созданию каждой карты. Знак для
    # рассылки достаётся запросом, объект карты в этом месте не нужен.
    #
    # SET NULL, а не CASCADE: удаление карты гасит признак, но не уносит
    # с собой пользователя.
    primary_chart_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("charts.id", ondelete="SET NULL")
    )

    # Тестировщику полный разбор открыт без оплаты — нужно для обкатки
    # в закрытой группе до запуска платежей.
    is_tester: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sql_text("false")
    )

    # --- Рассылки --------------------------------------------------------
    # Выбор ПОЛЬЗОВАТЕЛЯ: отписался от рассылки, но сервисом пользуется.
    subscribed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sql_text("true")
    )

    # Разрешил сообществу VK писать ему (VKWebAppAllowMessagesFromGroup).
    # Ведём сами, потому что через API список разрешивших не отдаётся вовсе
    # (docs/astro-context.md) — не записали в момент нажатия, потеряли
    # навсегда. NULL — не разрешал или отозвал разрешение.
    vk_messages_allowed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    # Техническая невозможность доставки: человек заблокировал бота, занёс
    # сообщество в чёрный список или закрыл себя настройками приватности.
    # Узнать об этом заранее нельзя — Telegram сообщает только в ответ на
    # попытку отправки (403 Forbidden), ВКонтакте — ошибками 900 и 902 в
    # ответе messages.send. Поэтому факт запоминаем: иначе каждая рассылка
    # снова стучится в мёртвые чаты, съедая лимит ~30 сообщений в секунду,
    # из-за которого живые получают её позже.
    #
    # Ошибка 901 сюда НЕ идёт: она про отозванное разрешение писать, его
    # место — vk_messages_allowed_at выше.
    #
    # Время, а не bool: «когда заблокировали» — это метрика оттока, по ней
    # видно, после какой именно рассылки люди уходили. Вернулся и написал
    # сам — сбрасываем в NULL.
    bot_blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # --- Активность ------------------------------------------------------
    # Последнее действие: сообщение боту, нажатие кнопки, открытие Mini App.
    # Нужен для трёх вещей: живой размер базы, реактивация («не заходил
    # 30 дней, но карта посчитана»), сегментация рассылок.
    #
    # Осознанная денормализация: то же самое даёт MAX(ts) по events, но
    # events — таблица-поток, и считать по ней максимум для каждого
    # пользователя на каждой рассылке со временем становится дорого.
    #
    # Обновляется НЕ на каждое действие, а не чаще раза в 6 часов (порог —
    # константа в сервисе пользователей). Все вопросы к этому полю живут на
    # масштабе дней и недель, там шесть часов неотличимы от нуля, зато
    # записей в базу на два порядка меньше.
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # --- Удаление данных -------------------------------------------------
    # Означает не «пользователя нет», а «персональные данные стёрты по его
    # просьбе»: обнуляем name, каскадом сносим charts (там дата и место
    # рождения), строку users оставляем.
    #
    # Так сделано ровно из-за уникальной пары выше: если удалить строку,
    # вернувшийся человек придёт с тем же (platform, external_id) и упрётся
    # в конфликт — а регистрации у нас нет, отказать ему не в чем. Плюс
    # сохраняется история воронки. Вернулся — снимаем отметку.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    @property
    def is_reachable(self) -> bool:
        """Можно ли отправить этому пользователю рассылку.

        Три условия разной природы: согласие (subscribed), техническая
        доставляемость (bot_blocked_at) и наличие самого пользователя
        (deleted_at). Собраны в одном месте, чтобы рассылка не забыла
        какое-нибудь из них.
        """
        return self.subscribed and self.bot_blocked_at is None and not self.is_deleted

    # Карты пользователя. lazy="raise_on_sql" — защита от главной ловушки
    # async-ORM: обычный relationship при первом обращении молча уходит в базу,
    # а в async это запрещено и падает MissingGreenlet где-нибудь в шаблоне
    # ответа. С этим режимом доступ разрешён, только если связь уже загружена
    # явно (selectinload), иначе — понятная ошибка сразу, а не под нагрузкой.
    #
    # passive_deletes=True: удаление строки users не тянет charts в память,
    # чтобы гасить их по одной — за каскад отвечает сама база (ondelete ниже).
    charts: Mapped[list[Chart]] = relationship(
        back_populates="user",
        # Строкой, а не объектом колонки: Chart здесь импортирован только для
        # проверки типов. Причина самого аргумента — в chart.py, у Chart.user.
        foreign_keys="Chart.user_id",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise_on_sql",
    )

    # Заказы, в отличие от карт, каскадом НЕ удаляются: нет ни cascade,
    # ни delete-orphan — за это отвечает ondelete="RESTRICT" в orders.
    # То есть пользователя со списанными деньгами база просто не даст
    # удалить, и это правильно (см. комментарий в Order.user_id).
    orders: Mapped[list[Order]] = relationship(back_populates="user", lazy="raise_on_sql")

    def __repr__(self) -> str:
        return f"<User id={self.id} {self.platform.value}:{self.external_id}>"
