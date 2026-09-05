"""Посты сообщества VK: очередь готовых текстов на публикацию.

Единственная таблица пакета, ни с кем не связанная: пост ничей. Заполняет
её app/services/content.py, показывает /admin/content, откуда текст
копируется в отложку VK руками. Общие соглашения — в app/models/__init__.py.
"""

from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class PostKind(str, enum.Enum):
    """Формат поста в сообществе — он же ключ к модулю промпта.

    Значения совпадают с именами модулей в app/llm/prompts/: реестр в
    services/content.py связывает их напрямую, как _PROMPTS в reports.py.

    DAILY_SKY — обзор неба дня одним текстом, единственный пост в ленту.
    DAILY_HOROSCOPE — гороскоп дня на 12 знаков; с 05.09.2026 идёт только
    в рассылку по личным сообщениям, в ленту не публикуется.
    ASTRO_EVENT — пост про одно событие календаря (лунация, затмение, станция).
    SIGN_PORTRAIT, CELEBRITY, INVITE — форматы из контент-сетки, промптов у них
    ещё нет; заведены сразу, чтобы добавление формата не требовало правки CHECK.
    """

    DAILY_SKY = "daily_sky"
    DAILY_HOROSCOPE = "daily_horoscope"
    ASTRO_EVENT = "astro_event"
    SIGN_PORTRAIT = "sign_portrait"
    CELEBRITY = "celebrity"
    INVITE = "invite"


class Post(Base):
    """Готовый текст для ленты сообщества VK.

    Пост ничей: ни пользователя, ни карты — поэтому reports не годится, там
    chart_id и user_id NOT NULL. Отсюда же отсутствие всяких связей.

    Адрес строки — (kind, target_date, event_key, prompt_version), и он
    уникален по той же причине, что у отчётов: генератор запускают на неделю
    вперёд, потом перезапускают, дописав пару дней, и второй раз платить
    DeepSeek за уже написанный текст мы не должны.

    Событие входит в адрес, а не только дата: 30.06.2026 в один день попадают
    полнолуние и вход Юпитера в знак, и это два разных повода. Без event_key
    адрес разрешал бы ровно один пост в сутки, то есть устройство базы решало
    бы за редактуру, сколько постов выходит. Сколько их — решает
    services/content.py.

    Строки живут два месяца, дальше их удаляет сам генератор (отдельный cron —
    лишняя точка отказа ради одного DELETE). Срок задан не текстом: гороскоп
    протухает за сутки, а вместе со строкой уходит расход токенов, и он нужен
    для сверки себестоимости за полный прошлый месяц.

    Чего здесь нет намеренно: статуса (draft/approved/published) и vk_post_id.
    Публикация ручная, через отложку VK (wall.post групповым ключом не
    вызывается), факт публикации — действие человека в окне браузера, и
    заполнять эти колонки нечем. Появятся вместе с правом wall, если его дадут
    (docs/astro-context.md, «Контур сообщества VK»).
    """

    __tablename__ = "posts"

    __table_args__ = (
        UniqueConstraint("kind", "target_date", "event_key", "prompt_version"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    kind: Mapped[PostKind] = mapped_column(
        SAEnum(
            PostKind,
            name="post_kind",
            native_enum=False,
            create_constraint=True,
            length=24,
            values_callable=lambda cls: [m.value for m in cls],
        ),
        nullable=False,
    )

    # День, которому пост адресован, по московской зоне — второе исключение из
    # правила «все времена TIMESTAMPTZ», рядом с birth_local. Пост относится к
    # суткам («гороскоп на 23 августа»), а не к моменту; хранить момент значило
    # бы пересчитывать зону при каждом чтении и однажды сдвинуть пост на сутки —
    # ровно ту ошибку чужих пабликов, из-за которой astro_core/transits.py
    # считает всё сразу в МСК.
    target_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Строка вида "daily_horoscope_v1" — константа VERSION из модуля промпта.
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)

    # Событие, о котором пост: вид вместе с участниками — "solar_eclipse",
    # "ingress:Jupiter", "aspect:Jupiter:Saturn:square". Собирает ключ
    # services/content.py, форма описана там же.
    #
    # Пустая строка (не NULL!) у форматов без события. NULL здесь тихо ломает
    # защиту от повторной оплаты: в уникальном индексе Postgres два NULL
    # считаются разными значениями, и второй гороскоп на ту же дату спокойно
    # записался бы рядом с первым.
    #
    # Заодно отвечает читающему пачку на вопрос «о чём посты»: по префиксу
    # видно, что три подряд получились про ретроградности.
    event_key: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", server_default=sql_text("''")
    )

    # Заголовок отдельной колонкой, хотя он же есть внутри text. Дублируем
    # сознательно: список пачки в /admin/content показывает строку на пост, и
    # ради заголовка ему пришлось бы разбирать JSON каждого. То же для чтения
    # руками из psql. У гороскопа дня заголовка нет — там NULL.
    title: Mapped[str | None] = mapped_column(Text)

    # Ответ модели как есть (у обоих промптов это JSON). Разбирают его parse()
    # и render() из модуля промпта — второй разбор в колонках разошёлся бы
    # с первым при первой же правке формата.
    text: Mapped[str] = mapped_column(Text, nullable=False)

    # --- Чем сгенерировано (себестоимость) --------------------------------
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    temperature: Mapped[float] = mapped_column(Float, nullable=False)

    tokens_prompt: Mapped[int] = mapped_column(Integer, nullable=False)
    tokens_completion: Mapped[int] = mapped_column(Integer, nullable=False)
    tokens_cached: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sql_text("0")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        event = f" {self.event_key}" if self.event_key else ""
        return f"<Post id={self.id} {self.kind.value}{event} {self.target_date} {self.prompt_version}>"
