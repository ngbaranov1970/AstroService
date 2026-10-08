"""Отчёт: готовый текст разбора и паспорт его генерации.

ReportType живёт здесь, хотя нужен ещё и заказам: разбор — это товар, и
order.py импортирует тип отсюда (связь односторонняя, цикла нет). Общие
соглашения пакета — в app/models/__init__.py.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy import (
    # Под именем sql_text, потому что в Report есть колонка text (текст
    # разбора) — она затенила бы функцию внутри тела класса.
    text as sql_text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.chart import Chart
    from app.models.pair import Pair


class ReportType(str, enum.Enum):
    """Вид разбора. Одновременно продукт (что покупают) и ключ кеша.

    FREE_NATAL — бесплатная приманка, короткий разбор натальной карты.
    FULL_NATAL — платный полный разбор посекционно.
    PAIR_FREE, PAIR_FULL — совместимость: тизер и платный разбор пары.
    Их адрес в кеше — pair_id, а не chart_id (см. индексы у Report).
    VIP_NATAL — разбор, который владелец правит руками и шлёт в личку;
    после оплаты не генерируется (docs/sales-policy.md).
    MATRIX, EASTERN — заготовки под Матрицу Судьбы и восточный гороскоп;
    заведены сразу, чтобы добавление продукта не требовало правки CHECK.
    """

    FREE_NATAL = "free_natal"
    FULL_NATAL = "full_natal"
    PAIR_FREE = "pair_free"
    PAIR_FULL = "pair_full"
    VIP_NATAL = "vip_natal"
    MATRIX = "matrix"
    EASTERN = "eastern"


class Report(Base):
    """Готовый текст разбора, написанный нейросетью.

    Одна строка — один текст. Таблица нужна для двух вещей.

    ПЕРВОЕ: не платить за один и тот же текст дважды.

    Маша получила бесплатный разбор утром, а вечером снова открыла бота.
    Если генерировать заново — мы опять платим DeepSeek, Маша опять ждёт
    полминуты, и текст приходит ДРУГОЙ (нейросеть каждый раз пишет
    по-новому), из-за чего выглядит, будто мы что-то напутали. Поэтому
    сначала ищем текст здесь и, если он есть, отдаём его.

    Ищем по «адресу» из трёх частей — это и есть уникальные индексы ниже:

        chart_id       — чья карта (у совместимости вместо неё pair_id)
        type           — какой продукт (бесплатный разбор, полный, …)
        prompt_version — по какой инструкции написан ("free_natal_v1")

    UNIQUE говорит базе: с таким адресом может быть только одна строка.
    Дубли становятся невозможны физически, а не «по договорённости».

    Версия промпта в адресе — чтобы правки инструкции не ломали старое.
    Переписал промпт потеплее, сменил VERSION на v2 → адрес новый → в базе
    его нет → текст генерируется заново, уже по новой инструкции. При этом
    старый (v1) остаётся лежать рядом, и два варианта можно сравнить.

    ВТОРОЕ: копить материал.

    Кроме текста пишем «паспорт» генерации — модель, temperature, токены:

    * токены это счёт от DeepSeek: сложив их, узнаём, во сколько нам
      обходится один разбор, и сколько бесплатных мы можем себе позволить;
    * temperature и модель нужны, когда на текст пожалуются: без них
      непонятно, при каких настройках получилась ерунда;
    * сами тексты — будущая выборка для пост-фильтра. Чтобы он ловил
      запреты модерации VK, надо сперва увидеть на сотнях реальных
      разборов, где именно нейросеть срывается. Эти сотни накапливаются
      здесь сами, пока идёт обкатка в Telegram.

    Оговорка: раз адрес уникален, повторная генерация с той же версией
    промпта строку ПЕРЕЗАПИШЕТ — история попыток не хранится. Нужно
    сравнивать варианты — заводится новая VERSION, для того она и есть.
    """

    __tablename__ = "reports"

    __table_args__ = (
        # Адрес отчёта: у обычного разбора это карта, у парного — пара.
        # Два ЧАСТИЧНЫХ индекса вместо одного общего, потому что в Postgres
        # два NULL в UNIQUE не равны: ограничение вида
        # (chart_id, pair_id, type, version) сняло бы защиту от дублей со
        # всех натальных отчётов разом, а на ней держится главное правило
        # «за один текст платим один раз».
        Index(
            "uq_reports_chart_type_version",
            "chart_id",
            "type",
            "prompt_version",
            unique=True,
            postgresql_where=sql_text("pair_id IS NULL"),
        ),
        Index(
            "uq_reports_pair_type_version",
            "pair_id",
            "type",
            "prompt_version",
            unique=True,
            postgresql_where=sql_text("pair_id IS NOT NULL"),
        ),
        # Под проверку лимита бесплатных генераций: COUNT по (user_id, type)
        # выполняется перед каждым разбором. Внешний ключ сам по себе индекс
        # в Postgres НЕ создаёт — без этой строки был бы полный проход по
        # таблице. Имя задаёт соглашение из app/db.py, поэтому None.
        Index(None, "user_id", "type"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # У парного разбора здесь лежит карта инициатора — якорь, а не адрес
    # кеша. Колонка остаётся NOT NULL, чтобы на ней продолжали держаться
    # денормализация user_id, каскад удаления и дневной потолок трат.
    chart_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("charts.id", ondelete="CASCADE"), nullable=False
    )

    # Заполнен только у совместимости; для натальных разборов NULL, и это
    # различие читают частичные индексы выше. CASCADE: в тексте парного
    # разбора персональные данные обоих, а пара сама уходит вместе с любой
    # из своих карт.
    pair_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("pairs.id", ondelete="CASCADE")
    )

    # Денормализация: владельца видно и через chart.user_id. Дублируем,
    # потому что главный частый вопрос — «сколько бесплатных разборов этого
    # типа человек уже получил», и он должен быть одним COUNT по
    # (user_id, type), а не джойном с charts на каждое сообщение боту.
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    type: Mapped[ReportType] = mapped_column(
        SAEnum(
            ReportType,
            name="report_type",
            native_enum=False,
            create_constraint=True,
            length=16,
            values_callable=lambda cls: [m.value for m in cls],
        ),
        nullable=False,
    )

    # Строка вида "free_natal_v1" — константа VERSION из модуля промпта.
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)

    # Text, а не String(N): у полного разбора нет осмысленного предела длины,
    # а в Postgres varchar(N) и text хранятся одинаково — ограничение сверху
    # ничего не экономит, зато однажды обрежет текст.
    text: Mapped[str] = mapped_column(Text, nullable=False)

    # --- Чем сгенерировано (корпус) --------------------------------------
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    temperature: Mapped[float] = mapped_column(Float, nullable=False)

    # Токены из LLMResult.usage: prompt_tokens и completion_tokens — это
    # прямая себестоимость разбора; cached — сколько из промпта DeepSeek
    # отдал из своего кеша (стабильный SYSTEM ради этого и сделан), по нему
    # видно, что кеширование действительно работает.
    tokens_prompt: Mapped[int] = mapped_column(Integer, nullable=False)
    tokens_completion: Mapped[int] = mapped_column(Integer, nullable=False)
    tokens_cached: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sql_text("0")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    chart: Mapped[Chart] = relationship(back_populates="reports", lazy="raise_on_sql")

    # Без back_populates: списка «тексты этой пары» продукт не показывает —
    # у пары их всегда максимум два, и берутся они по адресу кеша.
    pair: Mapped[Pair | None] = relationship(lazy="raise_on_sql")

    def __repr__(self) -> str:
        target = f"pair_id={self.pair_id}" if self.pair_id else f"chart_id={self.chart_id}"
        return f"<Report id={self.id} {target} {self.type.value}/{self.prompt_version}>"
