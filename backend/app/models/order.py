"""Заказ: намерение купить разбор и след того, чем оно кончилось.

Единственный файл пакета, где лежат денежные факты, — отсюда и правила
удаления строже, чем у соседей (RESTRICT вместо CASCADE). ReportType
импортируется из report.py: платят именно за вид разбора. Общие соглашения
пакета — в app/models/__init__.py.
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
    Integer,
    String,
    Text,
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
from app.models.report import ReportType

if TYPE_CHECKING:
    from app.models.chart import Chart
    from app.models.pair import Pair
    from app.models.user import User


class OrderStatus(str, enum.Enum):
    """Жизненный путь заказа. Переходы только вперёд, назад дороги нет.

        PENDING  — заказ создан, платёж в ЮKassa заведён, человек ушёл платить
        PAID     — деньги получены, отчёт разблокирован
        CANCELED — не заплатил: закрыл вкладку, банк отказал, истёк срок
        REFUNDED — деньги вернули по обращению

    Про REFUNDED: он заведён сразу, хотя возвратов мы пока не делаем. Строка
    в enum ничего не стоит, а её отсутствие в тот день, когда придёт первый
    возврат, означало бы миграцию под давлением.

    Чего здесь нет: WAITING_FOR_CAPTURE. У ЮKassa это состояние «деньги
    захолдированы, подтвердите списание» — оно нужно магазинам, которые
    сначала проверяют товар на складе. У нас товар — текст, он есть всегда,
    поэтому платёж создаётся с capture=true и подтверждается автоматически.
    """

    PENDING = "pending"
    PAID = "paid"
    CANCELED = "canceled"
    REFUNDED = "refunded"


class ClientGender(str, enum.Enum):
    """Пол клиента VIP-разбора — для рода в тексте («ты родилась»)."""

    FEMALE = "female"
    MALE = "male"


class Order(Base):
    """Заказ: намерение купить конкретный отчёт и след того, чем оно кончилось.

    Зачем отдельная таблица, если можно было бы поставить в reports галочку
    «оплачено»: заказов на один и тот же отчёт может быть несколько (первый
    раз не прошла карта, второй прошёл), и каждый из них — денежный факт,
    который надо уметь показать при разборе спора. Галочка хранит только
    последний исход и не отвечает на вопрос «а сколько он вообще платил».

    Как устроен путь оплаты (напоминание, почему поля именно такие):

    1. Человек жмёт «купить» в Mini App или в боте. Оплата ВНУТРИ Mini App
       запрещена (цифровые товары там продаются только за «голоса» с большой
       комиссией, на iOS — вообще нельзя), поэтому мы создаём строку здесь
       и даём ссылку на сайт: /pay/<token>.
    2. Страница оплаты на сайте по token находит заказ и отправляет человека
       в ЮKassa (URL, который та вернула, лежит в confirmation_url).
    3. ЮKassa стучится в наш вебхук: «платёж <provider_payment_id> успешен».
       Мы находим заказ по этому id, ставим PAID и paid_at — отчёт открыт.

    Обратите внимание: разблокировку даёт именно вебхук, а не возвращение
    человека на страницу «спасибо». Возврат с сайта банка — это просто
    редирект, его легко не дождаться (закрыл вкладку) и так же легко
    подделать, открыв нужный адрес руками.

    Про «комплект за 890 ₽»: сейчас один заказ = один отчёт. Когда появится
    комплект из нескольких разборов, это будет не JSON-массив в этой строке,
    а отдельная таблица позиций заказа — денежные данные должны оставаться
    проверяемыми внешними ключами, а не свободным JSON.
    """

    __tablename__ = "orders"

    __table_args__ = (
        # «Мои покупки» в профиле и админский вопрос «кто из этих людей
        # платил» — оба запроса идут по паре (user_id, status).
        Index(None, "user_id", "status"),
        # Ждущий оплаты заказ на один товар может быть только один. Два
        # одновременных нажатия «купить» иначе заводят два заказа и два
        # платежа в ЮKassa: оплатят один, второй останется висеть.
        # Индекс ЧАСТИЧНЫЙ (только pending), потому что оплаченных и
        # отменённых заказов на ту же тройку бывает сколько угодно — это
        # история покупок, и запрещать её нельзя.
        #
        # Индексов два, и различает их pair_id — по той же причине, что у
        # reports: у парного заказа chart_id заполнен картой-якорем, и один
        # общий индекс запретил бы человеку одновременно ждать оплаты
        # разборов «я + Маша» и «я + Лена». Выглядело бы как молчащая
        # кнопка «купить» на второй паре.
        Index(
            "uq_orders_one_pending",
            "user_id",
            "chart_id",
            "report_type",
            unique=True,
            postgresql_where=sql_text("status = 'pending' AND pair_id IS NULL"),
        ),
        Index(
            "uq_orders_one_pending_pair",
            "user_id",
            "pair_id",
            "report_type",
            unique=True,
            postgresql_where=sql_text("status = 'pending' AND pair_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # Публичный адрес заказа: он попадает в ссылку на страницу оплаты, то
    # есть его увидит и человек, и его почта, и, возможно, чат с друзьями.
    # Поэтому не id: последовательные номера позволяют подставить соседний
    # и увидеть чужой заказ. 24 случайных байта в urlsafe-виде подобрать
    # перебором нельзя.
    #
    # Значение генерирует Python, а не база: server_default тут был бы
    # gen_random_uuid(), а uuid и длиннее в URL, и завязывает нас на
    # конкретную СУБД ради значения, которое всё равно создаётся в момент
    # создания заказа сервисом.
    token: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        default=lambda: secrets.token_urlsafe(24),
    )

    # RESTRICT, а не CASCADE: заказ переживает пользователя. «Удалить мои
    # данные» у нас означает обнулить профиль и снести charts (см. User),
    # а не стереть историю платежей — её надо хранить и для бухгалтерии,
    # и на случай спора о возврате. RESTRICT заодно делает случайный
    # DELETE FROM users в psql громкой ошибкой вместо тихой потери денег.
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    # Карта, за разбор которой платят. Nullable и SET NULL, потому что
    # требования тут прямо конфликтуют: карту человек вправе удалить (это
    # его персональные данные), а заказ удалять нельзя (это деньги).
    # Компромисс — заказ остаётся, но уже без ссылки на данные рождения.
    chart_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("charts.id", ondelete="SET NULL")
    )

    # Пара, за разбор которой платят; у натальных заказов NULL. SET NULL по
    # той же причине, что у chart_id выше: пара уходит вместе с любой своей
    # картой, а денежный факт остаётся.
    #
    # У парного заказа заполнены ОБЕ колонки: chart_id хранит карту
    # инициатора (по ней заказ находится в «моих покупках» и попадает в
    # каскады), pair_id — за что именно заплачено.
    pair_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("pairs.id", ondelete="SET NULL")
    )

    # Что именно покупают. Тот же перечень, что у reports.type, — и это не
    # совпадение: после оплаты сервис ищет (или генерирует) отчёт ровно
    # с этим типом для этой карты.
    report_type: Mapped[ReportType] = mapped_column(
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

    # Сумма В КОПЕЙКАХ целым числом: 290 ₽ → 29000. Float для денег
    # использовать нельзя — 0.1 + 0.2 в двоичной дроби даёт не 0.3, и на
    # сверке с выпиской это вылезает копеечными расхождениями, которые
    # невозможно объяснить. Целые копейки складываются точно.
    #
    # Цена копируется в заказ, а не берётся из конфига в момент показа:
    # прайс поменяется, а этот человек заплатил столько, сколько заплатил.
    amount: Mapped[int] = mapped_column(Integer, nullable=False)

    # Колонки currency нет намеренно: ЮKassa для самозанятого принимает
    # только рубли, а колонка, у которой всегда одно значение, — это
    # обещание мультивалютности, которого мы не выполняем.

    status: Mapped[OrderStatus] = mapped_column(
        SAEnum(
            OrderStatus,
            name="order_status",
            native_enum=False,
            create_constraint=True,
            length=16,
            values_callable=lambda cls: [m.value for m in cls],
        ),
        nullable=False,
        default=OrderStatus.PENDING,
        server_default=sql_text("'pending'"),
    )

    # --- Сторона ЮKassa ---------------------------------------------------
    # Идентификатор платежа у них (uuid-строка). UNIQUE здесь — это защита
    # от двойного зачисления: ЮKassa повторяет вебхук, пока не получит 200,
    # и один и тот же платёж легко прилетает дважды. Найдя заказ по этому
    # полю и увидев, что он уже PAID, мы просто отвечаем 200 и ничего не
    # делаем. NULL до того, как платёж создан.
    provider_payment_id: Mapped[str | None] = mapped_column(String(64), unique=True)

    # Ссылка на форму оплаты, которую вернула ЮKassa. Храним, чтобы человек,
    # закрывший вкладку и вернувшийся по той же ссылке /pay/<token>, попал
    # в СВОЙ уже созданный платёж, а не породил второй на ту же сумму.
    confirmation_url: Mapped[str | None] = mapped_column(String(512))

    # Сырое тело последнего уведомления от ЮKassa как есть. Разбирать его
    # в колонки смысла нет — это их формат, он меняется без нашего участия;
    # а при споре «мы не получали от вас платёж» это единственное, что можно
    # положить на стол.
    provider_data: Mapped[dict | None] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Момент зачисления денег (по нашим часам, в UTC). Отдельно от status,
    # потому что вопрос «когда пришли деньги» — это выручка по дням, и
    # ответ на него не должен требовать разбора логов вебхука.
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # --- VIP-разбор (только report_type = vip_natal) ------------------------
    # Черновик лежит здесь, а не в reports: строка reports открыла бы текст
    # в приложении до правки. «В работе» = PAID и delivered_at IS NULL.
    draft_text: Mapped[str | None] = mapped_column(Text)
    # Вариант от DeepSeek (правка или новый черновик), ждущий «принять / отказаться».
    # Пока он не принят, draft_text не трогаем: сравнивать надо со старым.
    draft_candidate: Mapped[str | None] = mapped_column(Text)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Пол вводят в форме заказа; у заказов до 09.10.2026 он NULL.
    client_gender: Mapped[ClientGender | None] = mapped_column(
        SAEnum(
            ClientGender,
            name="client_gender",
            native_enum=False,
            create_constraint=True,
            length=8,
            values_callable=lambda cls: [m.value for m in cls],
        )
    )

    @property
    def is_paid(self) -> bool:
        """Открывать ли отчёт. Возврат снова закрывает доступ."""
        return self.status is OrderStatus.PAID

    @property
    def amount_rub(self) -> str:
        """Сумма для показа человеку: 29000 → "290.00" (и для ЮKassa тоже)."""
        return f"{self.amount / 100:.2f}"

    user: Mapped[User] = relationship(back_populates="orders", lazy="raise_on_sql")
    chart: Mapped[Chart | None] = relationship(back_populates="orders", lazy="raise_on_sql")

    # Без back_populates: список «заказы этой пары» нигде не показывается,
    # а обратная связь потребовала бы объявления и в Pair.
    pair: Mapped[Pair | None] = relationship(lazy="raise_on_sql")

    def __repr__(self) -> str:
        return (
            f"<Order id={self.id} user_id={self.user_id} "
            f"{self.report_type.value} {self.amount_rub}₽ {self.status.value}>"
        )
