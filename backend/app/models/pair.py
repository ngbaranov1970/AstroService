"""Пара: две карты, тип связи и посчитанная по ним синастрия.

Относится к reports так же, как charts: здесь «расчёт», там «тексты по
расчёту». Персональных данных своих не хранит — они в обеих картах, — но
calc из них выведен, поэтому удаление любой стороны сносит пару каскадом.
Общие соглашения пакета — в app/models/__init__.py.
"""

from __future__ import annotations

import enum
import secrets
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.chart import Chart
    from app.models.user import User


class PairKind(str, enum.Enum):
    """Тип связи. Обязательное поле, а не украшение.

    Промпт, по умолчанию предполагающий влечение, на паре «я и мама» выдаст
    дикость и готовую жалобу. Побочная выгода — ещё один различающий вход
    для текста, а универсальность у синастрии главный риск.

    Видов ровно два. Дружба и работа были в плане, но сняты 12.09.2026:
    рабочая совместимость аудитории неинтересна, а дружба без влечения
    сводится к той же семейной рамке. Вернуть вид — это значение здесь,
    рамка в обоих промптах пары и правка CHECK на колонке.
    """

    ROMANCE = "romance"
    FAMILY = "family"


class Pair(Base):
    """Пара из двух карт: вход синастрии и её результат.

    Одна строка — «моя карта + карта партнёра + тип связи» плюс всё, что из
    них посчитано. Стороны НЕ симметричны и не нормализуются: A — инициатор
    («я»), B — партнёр, текст разбора асимметричен («его Сатурн на её Луне»
    и наоборот — разные сюжеты), и склейка сделала бы стороны неразличимыми.

    Пара живёт с незаполненной стороной B: человек позвал партнёра ссылкой,
    тот ещё не пришёл. Это то же самое, что заказ в статусе pending, поэтому
    chart_b_id nullable, а не отдельная таблица invites. Приход партнёра
    тогда — один UPDATE ... WHERE chart_b_id IS NULL, и гонка «открыл ссылку
    в двух вкладках» закрывается базой, а не кодом.

    Почему расчёту нужна своя таблица, а не место в charts.calc: карта
    дедуплицируется по данным ОДНОГО человека, и результат, зависящий от
    второго, в неё не помещается по смыслу.
    """

    __tablename__ = "pairs"

    __table_args__ = (
        # Дедуп собранной пары: те же две карты и тот же тип связи → та же
        # строка, иначе повторное «посчитать» плодило бы пары, а с ними и
        # платные отчёты. Индекс ЧАСТИЧНЫЙ, потому что незавершённых
        # инвайтов на одну карту законно бывает несколько — позвали двоих.
        #
        # kind в ключе: он идёт в промпт и меняет текст, а кеш отчёта
        # ключуется по pair_id. Без kind смена «романтика» → «работа» молча
        # вернула бы прежний текст про влечение.
        Index(
            "uq_pairs_complete",
            "user_id",
            "chart_a_id",
            "chart_b_id",
            "kind",
            unique=True,
            postgresql_where=sql_text("chart_b_id IS NOT NULL"),
        ),
        # Под список «мои пары» на экране совместимости. Внешний ключ сам по
        # себе индекс в Postgres не создаёт.
        Index(None, "user_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # Публичный адрес пары — тот же приём, что у Chart.token: по
    # последовательному id подставляется соседнее число, а за ним данные
    # рождения двух человек.
    #
    # В отличие от карты, эту ссылку пересылать НЕЛЬЗЯ: карта — данные
    # одного человека, и он сам решает, кому её показать, а пара — данные
    # двоих, и второй согласия не давал. Поэтому GET /pairs/{token}
    # проверяет владельца, хотя GET /charts/{token} намеренно не проверяет.
    token: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        default=lambda: secrets.token_urlsafe(24),
    )

    # Инициатор: тот, кто собрал пару и кому принадлежит сторона A.
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    # CASCADE у обеих сторон: в calc лежат производные от даты и места
    # рождения обоих — те же персональные данные, и после «удалите меня»
    # хранить их нельзя.
    chart_a_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("charts.id", ondelete="CASCADE"), nullable=False
    )

    # NULL, пока ждём партнёра по инвайт-ссылке.
    chart_b_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("charts.id", ondelete="CASCADE")
    )

    kind: Mapped[PairKind] = mapped_column(
        SAEnum(
            PairKind,
            name="pair_kind",
            native_enum=False,
            create_constraint=True,
            length=8,
            values_callable=lambda cls: [m.value for m in cls],
        ),
        nullable=False,
    )

    # Как назвать инициатора партнёру на анонимном экране приглашения.
    # Отдельное поле, а не chart_a.person_name: имя карты человек вводил про
    # себя и для себя, и показ его третьему лицу — утечка персональных
    # данных. NULL — покажем безличное «вас приглашают».
    inviter_label: Mapped[str | None] = mapped_column(String(64))

    # --- Результат -------------------------------------------------------
    # Выход compute_synastry: meta с таблицей орбов, aspects и summary.
    # NULL, пока второй стороны нет — считать нечего.
    calc: Mapped[dict | None] = mapped_column(JSONB)

    # Версия синастрии (astro_core.synastry.VERSION), своя и отдельная от
    # charts.calc_version: орбы и веса здесь наши, и меняться они будут не
    # тогда, когда обновится kerykeion.
    calc_version: Mapped[str | None] = mapped_column(String(32))

    # --- Приглашение партнёра --------------------------------------------
    # По смыслу пароль: кто знает строку, тот открывает анонимный экран и
    # вписывает вторую сторону. Поэтому в логи не идёт никогда и живёт
    # отдельно от token — тот принадлежит инициатору и не протухает.
    invite_token: Mapped[str | None] = mapped_column(String(64), unique=True)

    # Срок жизни ссылки. Без него забытое приглашение остаётся открытым
    # входом в аккаунт инициатора навсегда.
    invite_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Когда партнёр пришёл. Отдельно от chart_b_id, потому что отвечает на
    # другой вопрос: сколько приглашений доходит до заполнения — это и есть
    # мера виральности, ради которой раздел делается.
    invite_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    @property
    def is_complete(self) -> bool:
        """Есть ли вторая сторона — то есть можно ли считать и писать текст."""
        return self.chart_b_id is not None

    # foreign_keys обязателен: между pairs и charts два внешних ключа, без
    # указания SQLAlchemy падает AmbiguousForeignKeysError на первом запросе.
    #
    # Обратных связей (Chart.pairs, User.pairs) нет намеренно: списка «пары
    # этой карты» продукт нигде не показывает, а за каскадное удаление
    # отвечает сама база через ondelete выше.
    chart_a: Mapped[Chart] = relationship(foreign_keys=[chart_a_id], lazy="raise_on_sql")
    chart_b: Mapped[Chart | None] = relationship(
        foreign_keys=[chart_b_id], lazy="raise_on_sql"
    )
    user: Mapped[User] = relationship(lazy="raise_on_sql")

    def __repr__(self) -> str:
        return (
            f"<Pair id={self.id} user_id={self.user_id} "
            f"{self.chart_a_id}+{self.chart_b_id} {self.kind.value}>"
        )
