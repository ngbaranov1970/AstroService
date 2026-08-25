"""Карта: данные рождения и всё, что по ним посчитано.

Здесь сосредоточены персональные данные — поэтому «удалить мои данные»
означает снести строки этой таблицы, а users оставить. Общие соглашения
пакета — в app/models/__init__.py.
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.order import Order
    from app.models.report import Report
    from app.models.user import User


class Chart(Base):
    """Расчёт по конкретным данным рождения: входные данные + результат.

    Одна строка — это «дата, время и место» плюс всё, что из них посчитано.
    Разделение с reports принципиальное: расчёт детерминирован и вечен
    (Солнце в момент рождения не переедет), а текст разбора зависит от
    версии промпта и модели и живёт отдельно.

    Персональные данные (когда и где родился) сосредоточены именно здесь —
    поэтому «удалить мои данные» = снести charts, а строку users оставить.

    Про кеш между пользователями: карта принадлежит пользователю, и два
    человека с совпадающими данными рождения получат две строки и два
    похода в LLM. Это осознанно: совпадение с точностью до минуты и города
    практически не встречается, а глобально общая карта не давала бы
    удалить данные одного человека, не задев другого.
    """

    __tablename__ = "charts"

    __table_args__ = (
        # Тот же пользователь, те же данные рождения → та же строка.
        # Без этого «посчитай ещё раз» плодило бы дубли карт, а вместе
        # с ними и дубли отчётов — то есть лишние деньги за DeepSeek.
        UniqueConstraint("user_id", "input_hash"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # Публичный адрес карты: попадает в ссылку /r/<token>, по которой человек
    # открывает свой разбор. Приём и причина те же, что у Order.token ниже:
    # по последовательному id можно подставить соседнее число и прочитать
    # чужую дату и место рождения, а это персональные данные. 24 случайных
    # байта перебором не подобрать.
    #
    # Почему токен на карте, а не на отчёте: страница /r/ показывает карту
    # вместе с текстом, а текстов у одной карты со временем станет несколько
    # (бесплатный, полный, совместимость) — все они откроются по одному
    # адресу, и второй токен не понадобится.
    #
    # Ссылку можно переслать: кто её знает, тот видит разбор. Для тестового
    # стенда это осознанно (тестер показывает разбор подруге); когда появится
    # оплата, сюда добавится проверка владения.
    token: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        default=lambda: secrets.token_urlsafe(24),
    )

    # Чья это карта — необязательное имя, как его ввёл пользователь.
    #
    # Расчёту не нужно, нужно людям: у одного тестера бывает карта своя и
    # мужа, и в списке их надо различать. В админке это первая колонка —
    # без неё сравниваются «карта №3» и «карта №7» вместо Маши и Пети.
    #
    # В промпт НЕ идёт: обращение по имени сбивает разбор в псевдоличную
    # переписку, а нам нужен текст про карту.
    person_name: Mapped[str | None] = mapped_column(String(64))

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        # ondelete="CASCADE" — правило внутри Postgres: удалили пользователя,
        # база сама снесла его карты. Работает и когда строку удаляют мимо
        # ORM, руками из psql.
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # --- Входные данные --------------------------------------------------
    # Локальное время рождения, БЕЗ таймзоны — то самое исключение из общего
    # правила «всё в UTC». Человек родился «11 мая в 17:20 по местным
    # часам»; в UTC это переводится только вместе с зоной и датой (декретное
    # время, отменённые переводы стрелок), чем и занимается astro_core/geo.
    # Хранить сразу UTC значило бы выбросить исходные показания часов
    # и потерять возможность пересчитать карту, если tzdata уточнится.
    birth_local: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)

    # Время рождения знают далеко не все. Без него нельзя считать дома,
    # Асцендент и MC — а это половина натальной карты. Договорённость:
    # при False в birth_local лежит 12:00, дома в разбор не идут, промпт
    # обязан об этом знать. Флаг входит в input_hash: «12:00 точно» и
    # «время неизвестно» — разные карты, хотя расчёт совпадает.
    time_known: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sql_text("true")
    )

    # Название места так, как его увидел пользователь при выборе города.
    # Для расчёта не используется (считаем по координатам) — нужно, чтобы
    # показать карту и дать человеку заметить, что он выбрал не тот Ростов.
    place_name: Mapped[str] = mapped_column(String(128), nullable=False)

    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)

    # Имя зоны IANA ("Europe/Moscow"), а не смещение: смещение зависит от
    # даты, имя — нет. Ровно то, что возвращает astro_core/geo.py.
    tz: Mapped[str] = mapped_column(String(64), nullable=False)

    # Отпечаток входных данных (sha256 в hex) для быстрого поиска дубля:
    # искать по паре (user_id, input_hash) дешевле и надёжнее, чем
    # сравнивать пять полей, где два — числа с плавающей точкой.
    # Что именно попадает в хеш, решает сервис (app/services/charts.py) —
    # он единственный источник правды, чтобы правило не разъехалось.
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    # --- Результат -------------------------------------------------------
    # JSONB, а не JSON: Postgres хранит его разобранным, умеет индексировать
    # и искать по ключам. Стоимость — потеря порядка ключей, которая нам
    # безразлична.
    #
    # Контейнер под ВСЕ виды расчёта по этим данным рождения:
    # {"natal": {...}}, позже добавятся "matrix" и "eastern". Входные данные
    # у них общие, поэтому это одна строка с несколькими ключами, а не три
    # таблицы.
    calc: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # Версия расчётного ядра. Обновление kerykeion, pyswisseph или tzdata
    # может сдвинуть цифры на доли минуты; по этому полю видно, какие карты
    # посчитаны чем, и можно выборочно пересчитать старые, не трогая свежие.
    calc_version: Mapped[str] = mapped_column(String(32), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # foreign_keys обязателен: после появления users.primary_chart_id между
    # таблицами ДВА внешних ключа, и без указания SQLAlchemy не знает, по
    # какому строить связь, — падает AmbiguousForeignKeysError при первом же
    # запросе. Здесь связь всегда по владельцу карты.
    user: Mapped[User] = relationship(
        back_populates="charts", foreign_keys=[user_id], lazy="raise_on_sql"
    )
    reports: Mapped[list[Report]] = relationship(
        back_populates="chart",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise_on_sql",
    )

    # passive_deletes=True без cascade: удаляя карту, SQLAlchemy по умолчанию
    # сам загрузил бы все её заказы и занулил им chart_id по одному. Здесь
    # это лишняя работа — то же самое делает ondelete="SET NULL" в базе.
    orders: Mapped[list[Order]] = relationship(
        back_populates="chart",
        passive_deletes=True,
        lazy="raise_on_sql",
    )

    def __repr__(self) -> str:
        return f"<Chart id={self.id} user_id={self.user_id} {self.birth_local:%Y-%m-%d %H:%M} {self.place_name}>"
