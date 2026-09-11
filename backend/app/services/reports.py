"""Отчёты: отдать готовый текст из базы или сгенерировать новый.

Это третья и последняя продуктовая склейка перед веб-стендом. Цепочка
целиком выглядит так:

    users.get_or_create()  →  charts.create_chart()  →  ЭТОТ МОДУЛЬ

Слева от него — кто человек и что у него за карта, справа — уже показ
текста на экране. Здесь соединяются три вещи, которые по отдельности друг
о друге не знают: таблица reports (app/models/report.py), шаблоны промптов
(app/llm/prompts/) и транспорт к DeepSeek (app/llm/client.py).

Про Telegram, VK и HTTP тут, как и в соседних сервисах, ни слова — граница
из CLAUDE.md. Эти же функции вызовет бот и будущий Mini App.

ГЛАВНОЕ ПРАВИЛО МОДУЛЯ: ЗА ОДИН ТЕКСТ ПЛАТИМ ОДИН РАЗ

Генерация стоит денег и полминуты времени, поэтому каждый текст имеет
«адрес» из трёх частей — (chart_id, type, prompt_version) — и по этому
адресу в базе может лежать только одна строка (UniqueConstraint в модели
Report). У разборов совместимости первая часть адреса другая: pair_id
вместо chart_id, потому что текст описывает двоих (индексы в модели
Report на это и разведены). Дальше всё одинаково, и чаще всего «адрес»
ниже читается как «карта или пара». Человек открыл разбор второй раз —
отдаём тот же текст, а не
пишем новый: иначе мы платим дважды, он ждёт дважды, и текст приходит
ДРУГОЙ (нейросеть каждый раз пишет по-новому), будто мы что-то напутали.

Версия промпта в адресе — чтобы правки инструкции не ломали старое.
Поменяли VERSION в модуле промпта → адрес новый → в базе его нет → текст
генерируется заново, уже по новой инструкции, а старый остаётся рядом
для сравнения.

ПОЧЕМУ ЗДЕСЬ ЕСТЬ ЗАМОК (_generation_lock)

Кеша в базе мало. Веб-страница разбора устроена так: пока текста нет, она
пишет «готовим разбор» и обновляет себя каждые пять секунд. Человек ещё
и сам жмёт F5. Каждое такое обновление может застать момент, когда
генерация уже идёт, но строки в базе ЕЩЁ НЕТ (она появится через минуту,
когда DeepSeek ответит) — и запустить вторую генерацию того же текста.
Оплаченную вторично.

База от этого не спасает: UNIQUE ловит дубль в момент вставки, то есть
когда за оба текста уже заплачено. Поэтому вторая линия обороны —
внутрипроцессный замок: параллельные запросы за одним и тем же адресом
выстраиваются в очередь, первый генерирует, остальные получают его
результат. Замок живёт в памяти процесса и переживает только его: два
процесса (api и бот, два воркера) друг о друге не знают. Для стенда этого
достаточно, а на проде генерация уедет в очередь задач — тогда замок
отсюда исчезнет.

ЧЕГО ЗДЕСЬ НЕТ: СТАТУСА «ГЕНЕРИРУЕТСЯ»

Колонки «состояние» у отчёта нет намеренно: строка появляется только
с готовым текстом. Для страницы это значит «нет строки — значит, ещё
готовим», и отдельная миграция не нужна. Плата за простоту: отличить
«считаем» от «упало с ошибкой» по базе нельзя — про ошибку узнаёт только
тот, кто вызвал get_or_create_report() и поймал исключение.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import ModuleType

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.llm import client
from app.llm.prompts import free_natal, full_natal
from app.models import Chart, Pair, Report, ReportType, User
from app.services import events, orders

# Логи здесь важнее, чем в соседних модулях: генерацию запускает фоновая
# задача, которую никто не ждёт, а колонки «статус» у отчёта нет намеренно
# (см. шапку). Значит по базе «идёт генерация» и «генерация упала»
# неразличимы — единственный след аварии остаётся в логе.
logger = logging.getLogger(__name__)

# --- Какой промпт отвечает за какой тип отчёта ---------------------------
#
# Реестр вместо цепочки if: добавить Матрицу Судьбы будет означать «написать
# модуль промпта и дописать сюда строку», а не править логику генерации.
#
# Каждый модуль промпта обязан иметь VERSION, SYSTEM и build_user() — это
# контракт, описанный в шапке app/llm/prompts/free_natal.py.
_PROMPTS: dict[ReportType, ModuleType] = {
    ReportType.FREE_NATAL: free_natal,
    ReportType.FULL_NATAL: full_natal,
}

def _limits() -> dict[ReportType, int]:
    """Сколько раз одному человеку можно СГЕНЕРИРОВАТЬ отчёт каждого типа.

    Тип, которого в словаре нет, счётчиком не ограничен. Считаются генерации,
    а не просмотры: повторное открытие готового разбора берётся из кеша.
    Полного разбора здесь нет никогда — его держит оплата (_PAID_TYPES).
    """
    # Настройки читаем при вызове, а не при импорте модуля: тесты и alembic
    # поднимают окружение позже, чем импортируется этот файл.
    limit = get_settings().free_natal_limit
    # FREE_NATAL_LIMIT=0 (дефолт с 01.09.2026) означает «не ограничивать»:
    # тип просто не попадает в словарь. Траты держит дневной потолок сервиса.
    return {ReportType.FREE_NATAL: limit} if limit > 0 else {}

# Платные типы: сгенерировать можно после оплаты этой карты (или тестеру).
# Проверка — оплаченный заказ в services/orders.py, а не галочка в отчёте:
# заказов на один текст бывает несколько, и каждый денежный факт нужен целым.
_PAID_TYPES: frozenset[ReportType] = frozenset(
    {ReportType.FULL_NATAL, ReportType.PAIR_FULL}
)

# Типы, адресуемые парой, а не картой. Список короткий и без реестра можно
# было бы обойтись проверкой имени, но именно по нему supported_types()
# решает, что показывать на главной: там плитки строятся прямо из ответа
# (frontend/src/.../homeTiles.js), и парный тип дал бы плитку в никуда.
_PAIR_TYPES: frozenset[ReportType] = frozenset(
    {ReportType.PAIR_FREE, ReportType.PAIR_FULL}
)

# Как разбор называется для человека. Отличается от TITLES в services/orders.py:
# там название товара для банковской выписки («Полный разбор натальной карты»),
# здесь подпись блока на экране.
_TITLES: dict[ReportType, str] = {
    ReportType.FREE_NATAL: "Бесплатный разбор",
    ReportType.FULL_NATAL: "Полный разбор",
    ReportType.MATRIX: "Матрица Судьбы",
    ReportType.EASTERN: "Восточный гороскоп",
    ReportType.PAIR_FREE: "Совместимость: коротко",
    ReportType.PAIR_FULL: "Разбор совместимости",
}


class ReportError(RuntimeError):
    """Общий предок ошибок модуля — чтобы вызывающий мог поймать одним except."""


class ReportLimitError(ReportError):
    """Лимит бесплатных генераций исчерпан.

    Отдельный класс, а не общая ошибка: это НЕ поломка, а нормальный исход,
    на который интерфейс отвечает человеческим текстом («бесплатные разборы
    закончились»), а не страницей 500.
    """


class ReportBudgetError(ReportError):
    """Сервис выбрал дневной потолок генераций — не этот человек, а все сразу.

    От ReportLimitError отличается адресатом: там «ваши разборы кончились»,
    здесь «сегодня мы уже не считаем», и человек ни в чём не виноват. Разные
    классы нужны, чтобы страница не сказала невиновному, что он исчерпал
    свой лимит, а в логе было видно, во что мы упёрлись.
    """


class ReportForbiddenError(ReportError):
    """Отчёт этого типа человеку пока не положен (не оплачен и он не тестер)."""


class ReportNotSupportedError(ReportError):
    """Для этого типа отчёта ещё не написан промпт (Матрица, восточный)."""


# --- Чтение готовых отчётов ----------------------------------------------


def _prompt_module(report_type: ReportType) -> ModuleType:
    module = _PROMPTS.get(report_type)
    if module is None:
        raise ReportNotSupportedError(
            f"Для отчёта «{report_type.value}» ещё нет модуля промпта "
            f"в app/llm/prompts/"
        )
    return module


def prompt_version(report_type: ReportType) -> str:
    """Текущая версия промпта для типа отчёта — третья часть адреса в кеше.

    Отдельная функция, потому что версия нужна не только генерации: страница
    разбора спрашивает «а есть ли уже текст», и спрашивать она должна ровно
    про ту версию, которой мы сейчас генерируем. Иначе после правки промпта
    страница вечно показывала бы старый текст.
    """
    return _prompt_module(report_type).VERSION


async def _find(
    session: AsyncSession,
    *,
    chart_id: int | None = None,
    pair_id: int | None = None,
    report_type: ReportType,
    version: str,
) -> Report | None:
    """Готовый текст по адресу. Адрес — либо карта, либо пара, ровно один."""
    if (chart_id is None) == (pair_id is None):
        raise ValueError("_find: нужен ровно один адрес — chart_id или pair_id")

    stmt = select(Report).where(
        Report.type == report_type,
        Report.prompt_version == version,
    )
    if pair_id is not None:
        stmt = stmt.where(Report.pair_id == pair_id)
    else:
        # pair_id IS NULL обязателен: у парного отчёта chart_id заполнен
        # картой инициатора, и без этого условия натальный запрос нашёл бы
        # чужой по смыслу текст. Тем же условием разведены индексы в модели.
        stmt = stmt.where(Report.chart_id == chart_id, Report.pair_id.is_(None))
    return await session.scalar(stmt)


async def get_report(
    session: AsyncSession, chart_id: int, report_type: ReportType
) -> Report | None:
    """Готовый отчёт по адресу (карта, тип, текущая версия промпта) или None.

    Именно этим вызовом страница /r/<token> отличает «текст готов» от «ещё
    готовим»: None означает, что генерация либо не запускалась, либо идёт
    прямо сейчас, либо упала — по базе эти случаи неразличимы (см. шапку).
    """
    return await _find(
        session,
        chart_id=chart_id,
        report_type=report_type,
        version=prompt_version(report_type),
    )


async def get_pair_report(
    session: AsyncSession, pair_id: int, report_type: ReportType
) -> Report | None:
    """То же, что get_report, но для разбора пары.

    Отдельная функция, а не необязательный аргумент у get_report: адрес
    ровно один из двух, и развилка внутри одной сигнатуры позволила бы
    позвать её без адреса вовсе.
    """
    return await _find(
        session,
        pair_id=pair_id,
        report_type=report_type,
        version=prompt_version(report_type),
    )


async def list_reports(session: AsyncSession, chart_id: int) -> list[Report]:
    """Все отчёты по карте, свежие первыми — включая старые версии промпта.

    Нужно админке стенда: положить рядом v1 и v2 одного разбора — ровно тот
    случай, ради которого версия входит в адрес.
    """
    stmt = (
        select(Report)
        .where(Report.chart_id == chart_id)
        .order_by(Report.created_at.desc())
    )
    return list(await session.scalars(stmt))


@dataclass(frozen=True, slots=True)
class Section:
    """Кусок готового разбора: заголовок (может отсутствовать) и текст.

    Единая форма для всех типов отчётов. Бесплатный разбор — сплошной текст,
    то есть одна секция без заголовка; полный приходит от модели готовым
    списком секций, потому что пейволл открывает первую и закрывает
    остальные, а резать сплошной текст по границам смысла нельзя.
    """

    title: str | None
    body: str


def sections(report: Report) -> list[Section]:
    """Разложить готовый отчёт на секции для показа.

    Живёт здесь, а не в веб-слое, по той же причине, что и реестр _PROMPTS:
    знание «какой тип отчёта чем разбирается» должно быть в одном месте.
    Страница просто рисует то, что ей дали, и не обязана знать, что
    full_natal отвечает JSON'ом, а free_natal — обычным текстом.

    Если разбор не удался (модель вернула не JSON или обрезала его на
    середине), отдаём весь текст одной секцией: показать оплаченный, пусть
    и кривой, ответ полезнее, чем страницу с ошибкой.
    """
    module = _PROMPTS.get(report.type)
    parser = getattr(module, "parse", None)
    if parser is not None:
        parsed = parser(report.text)
        if parsed is not None:
            return [Section(title=s["title"], body=s["body"]) for s in parsed]
        # WARNING, а не DEBUG: за этот ответ уже заплачено, и если такое
        # случается регулярно — виноват промпт, и это надо чинить.
        logger.warning(
            "Ответ %s/%s (отчёт %s) не разобрался на секции — показываю как есть",
            report.type.value, report.prompt_version, report.id,
        )
    return [Section(title=None, body=report.text)]


async def needs_purchase(
    session: AsyncSession,
    *,
    user: User | None,
    chart: Chart,
    report_type: ReportType,
    pair: Pair | None = None,
) -> bool:
    """Надо ли этому человеку заплатить за этот разбор этой карты или пары.

    user=None — читатель, которого мы не опознали (зашёл по ссылке и ничего
    ещё не нажимал): платный тип для него закрыт, покупок за ним нет.

    Нужна интерфейсу: страница обязана ЗАРАНЕЕ знать, показывать кнопку
    «купить» или готовый текст. Без неё веб-слой либо повторял бы у себя
    список платных типов, либо звал генерацию ради того, чтобы поймать
    ReportForbiddenError, то есть выяснял бы доступ отказом.

    Карта в аргументах потому, что платят за текст, а не за подписку:
    купленный полный разбор одной карты не открывает разбор второй.

    Про уже написанный текст ничего не говорит: он открывается независимо
    (проверки доступа стоят после кеша) — спрашивать надо только там, где
    текста ещё нет.
    """
    if report_type not in _PAID_TYPES:
        return False
    if user is None:
        return True
    if user.is_tester:
        return False
    if pair is not None:
        return not await orders.has_paid(
            session, user_id=user.id, pair_id=pair.id, report_type=report_type
        )
    return not await orders.has_paid(
        session, user_id=user.id, chart_id=chart.id, report_type=report_type
    )


def supported_types() -> list[ReportType]:
    """Разборы карты, которые сейчас можно получить, — те, у кого есть промпт.

    В ReportType заранее заведены MATRIX и EASTERN, чтобы добавление продукта
    не требовало миграции CHECK. Интерфейсу их показывать нельзя: генерация
    такого типа кончится ReportNotSupportedError.

    Парные типы отсюда исключены: главная строит плитки прямо из этого
    списка, а разбор пары начинается не с карты, а с выбора партнёра — и
    плитка на главной вела бы в никуда.
    """
    return [t for t in _PROMPTS if t not in _PAIR_TYPES]


def supported_pair_types() -> list[ReportType]:
    """То же для совместимости — их показывает экран пары, а не главная."""
    return [t for t in _PROMPTS if t in _PAIR_TYPES]


def title(report_type: ReportType) -> str:
    """Название разбора для человека — подпись блока на экране."""
    return _TITLES.get(report_type, "Астрологический разбор")


def section_titles(report_type: ReportType) -> list[str]:
    """Из чего состоит отчёт этого типа — для анонса до генерации.

    Пусто, если промпт состава не объявляет (free_natal отвечает сплошным
    текстом): вызывающий тогда просто не рисует список.
    """
    module = _PROMPTS.get(report_type)
    return list(getattr(module, "SECTION_TITLES", ()))


async def count_generated(
    session: AsyncSession, user_id: int, report_type: ReportType
) -> int:
    """Сколько отчётов этого типа человек уже сгенерировал.

    Считаем по паре (user_id, type) без джойна с charts — ради этого user_id
    продублирован в reports, и под эту пару в модели заведён индекс.

    Версия промпта в счёт не входит: сменили VERSION, текст сгенерировался
    заново — значит, мы заплатили ещё раз, и лимит обязан это видеть.
    """
    stmt = select(func.count()).where(
        Report.user_id == user_id, Report.type == report_type
    )
    return await session.scalar(stmt) or 0


async def count_generated_last_day(session: AsyncSession) -> int:
    """Сколько отчётов сервис сгенерировал за последние 24 часа — всего.

    Сутки скользящие, а не календарные: календарные обнуляются в полночь и
    дают всплеск трат сразу после неё, а нам нужен именно ровный расход.

    Считается по строкам reports, то есть по успешным генерациям. Упавшие
    (деньги потрачены, строки нет) сюда не попадают — они редки, а их след
    остаётся в логе как ERROR.
    """
    since = datetime.now(UTC) - timedelta(days=1)
    stmt = select(func.count()).where(Report.created_at >= since)
    return await session.scalar(stmt) or 0


# --- Замок на время генерации --------------------------------------------


@dataclass
class _LockEntry:
    """Замок для одного адреса отчёта, счётчик ждущих и уже готовый текст.

    СЧЁТЧИК нужен для уборки. Без него словарь замков рос бы вечно — по
    записи на каждый когда-либо сгенерированный отчёт. А удалять замок сразу
    после освобождения нельзя: в очереди за ним может стоять другой запрос,
    и, выбросив объект, мы дали бы следующему создать ВТОРОЙ замок на тот же
    адрес — то есть ровно ту параллельную генерацию, от которой защищаемся.
    Поэтому запись убирается, только когда ждущих не осталось.

    RESULT — ответ DeepSeek, оставленный тем, кто генерировал первым. Это
    не «ускорение», а обязательная часть защиты от двойной оплаты, и вот
    почему.

    Одного замка мало. Транзакцию коммитит ВЫЗЫВАЮЩИЙ, то есть уже после
    того, как мы вышли из замка и вернули ему отчёт. Значит, между вставкой
    строки и её коммитом есть промежуток, в котором замок уже свободен, а
    строки для чужой транзакции ещё не существует — незакоммиченное чужое
    в Postgres не видно. Второй запрос просыпается ровно в этот момент,
    проверяет базу, ничего не находит и честно платит за тот же текст
    второй раз. Проверено на живой базе: замок без result давал одну строку
    (UNIQUE сработал) и ДВА вызова DeepSeek.

    С result второй запрос в DeepSeek не идёт: он берёт готовый текст и
    пытается вставить свою строку. Дальше решает база — либо конкурент
    успел закоммитить и мы получаем IntegrityError и забираем его строку,
    либо конкурент откатился и наша строка становится единственной. Текст
    в обоих случаях не теряется, а оплачен он ровно один раз.
    """

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0
    result: client.LLMResult | None = None


_locks: dict[str, _LockEntry] = {}


def _lock_key(
    *, chart_id: int | None, pair_id: int | None, report_type: ReportType, version: str
) -> str:
    """Адрес отчёта строкой — ключ замка и реестра генераций.

    Префикс c/p обязателен: идентификаторы карт и пар нумеруются независимо,
    и без него пара 5 и карта 5 делили бы один замок — генерация разбора
    пары держала бы в очереди чужой натальный разбор.
    """
    owner = f"p{pair_id}" if pair_id is not None else f"c{chart_id}"
    return f"{owner}:{report_type.value}:{version}"


@asynccontextmanager
async def _generation_lock(key: str) -> AsyncIterator[_LockEntry]:
    """Встать в очередь за правом генерировать отчёт по этому адресу.

    Отдаёт саму запись, а не просто держит замок: внутри нужен её result.
    """
    entry = _locks.get(key)
    if entry is None:
        entry = _locks[key] = _LockEntry()
    entry.users += 1
    try:
        async with entry.lock:
            yield entry
    finally:
        entry.users -= 1
        if entry.users == 0:
            _locks.pop(key, None)


# --- Генерация ------------------------------------------------------------


async def get_or_create_report(
    session: AsyncSession,
    *,
    chart: Chart,
    user: User,
    report_type: ReportType = ReportType.FREE_NATAL,
    name: str | None = None,
    pair: Pair | None = None,
) -> Report:
    """Главная функция модуля: вернуть текст разбора, сгенерировав при нужде.

    Возвращает либо готовый отчёт из базы (мгновенно), либо только что
    написанный DeepSeek (30–60 секунд). Для вызывающего разницы нет.

    pair задан — разбор совместимости: адресом кеша становится пара, вход
    промпта берётся из pair.calc, а chart обязан быть стороной A (он же
    остаётся в reports.chart_id якорем ради каскадов и счётчика трат).

    Почему user передаётся отдельно, хотя владелец известен из chart.user_id:
    нужен ещё и is_tester, а связи в моделях объявлены с lazy="raise_on_sql" —
    обращение к chart.user не сходит в базу молча, а падает. Так сделано
    нарочно, чтобы случайный доступ к связи не превращался в скрытый запрос
    посреди генерации. Вызывающий пользователя уже получил (с него начинается
    обработка любого запроса), так что лишней работы тут нет.

    Что может бросить:
        ReportLimitError        — бесплатные генерации кончились;
        ReportForbiddenError    — платный отчёт без оплаты и человек не тестер;
        ReportNotSupportedError — нет промпта под этот тип;
        LLMError (из client.py) — DeepSeek не ответил или ответ обрезан.
    Первые три — нормальные исходы, на которые у интерфейса есть текст;
    последняя — авария, на неё показывают «попробуйте ещё раз».

    Транзакцию не коммитим — как и в соседних сервисах, это дело вызывающего.
    Оговорка про соединение: пока идёт генерация, сессия остаётся открытой,
    то есть держит соединение к Postgres десятки секунд. На стенде с десятком
    тестеров это неважно; когда генерация уедет в фоновую очередь, у неё
    будет своя короткоживущая сессия.
    """
    module = _prompt_module(report_type)
    version: str = module.VERSION

    _check_address(chart=chart, report_type=report_type, pair=pair)
    pair_id = pair.id if pair is not None else None
    # Кого показываем в логах: пару или карту. Идентификаторы, а не имена —
    # персональных данных в логах нет ни на одном слое.
    subject = f"пары {pair_id}" if pair_id is not None else f"карты {chart.id}"

    # Шаг 1: быстрый путь. Текст по этому адресу уже есть — отдаём и уходим:
    # ни замка, ни лимитов, ни DeepSeek. По этому пути идёт большинство
    # запросов, потому что страница разбора перечитывает себя раз в пять
    # секунд, а текст пишется один раз.
    # Адрес кеша: у парного разбора это пара, у остальных — карта. chart_id
    # при паре передавать нельзя, _find принимает ровно один из двух.
    chart_key = None if pair_id is not None else chart.id
    cached = await _find(
        session,
        chart_id=chart_key,
        pair_id=pair_id,
        report_type=report_type,
        version=version,
    )
    if cached is not None:
        # DEBUG, а не INFO: страница перечитывает себя каждые пять секунд,
        # на INFO это залило бы лог одинаковыми строками.
        logger.debug("Кеш: отчёт %s/%s для %s уже есть", report_type.value,
                     version, subject)
        return cached

    # Шаг 2: очередь за замком. Ключ — тот самый адрес отчёта: генерации
    # РАЗНЫХ отчётов друг другу не мешают, а параллельные попытки написать
    # ОДИН выстраиваются в очередь.
    key = _lock_key(
        chart_id=chart.id, pair_id=pair_id, report_type=report_type, version=version
    )
    async with _generation_lock(key) as slot:
        # Пока стояли в очереди, конкурент мог всё сделать за нас и успеть
        # закоммитить — тогда его строка уже видна и работы не осталось.
        cached = await _find(
            session,
            chart_id=chart_key,
            pair_id=pair_id,
            report_type=report_type,
            version=version,
        )
        if cached is not None:
            return cached

        await _check_allowed(
            session, user=user, chart=chart, report_type=report_type, pair=pair
        )

        # Необязательные настройки промпта. getattr с дефолтом из client.py,
        # а не обязательное поле в каждом модуле: у большинства промптов
        # своих настроек нет, и дублировать общее значение — верный способ
        # однажды получить два разных «дефолта». У full_natal своя
        # temperature (0.8) и свой потолок токенов, у free_natal — общие.
        temperature: float = getattr(module, "TEMPERATURE", client.DEFAULT_TEMPERATURE)
        max_tokens: int = getattr(module, "MAX_TOKENS", 4096)

        # Шаг 3: текст. Либо его уже получил конкурент и оставил нам в slot
        # (см. _LockEntry.result — строки в базе может ещё не быть, а текст
        # уже оплачен), либо идём в DeepSeek сами. Здесь уходит почти всё
        # время вызова: 30–60 секунд.
        result = slot.result
        if result is None:
            user_message = await _user_message(
                session, module, chart=chart, pair=pair, name=name
            )
            logger.info(
                "Генерирую отчёт %s/%s для %s (пользователь %s, temperature=%s)",
                report_type.value, version, subject, chart.user_id, temperature,
            )
            started = time.perf_counter()
            try:
                result = await client.chat(
                    module.SYSTEM,
                    user_message,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except Exception:
                # Клиент уже написал в лог, ЧТО случилось, но не знает, для
                # какой карты: без этой строки авария не привязывается к
                # человеку, который смотрит на вечное «готовим разбор».
                logger.exception(
                    "Генерация %s/%s для %s провалилась через %.1f с",
                    report_type.value, version, subject, time.perf_counter() - started,
                )
                raise
            logger.info(
                "Отчёт %s/%s для %s готов за %.1f с, %d ток. ответа",
                report_type.value, version, subject,
                time.perf_counter() - started, result.usage.completion_tokens,
            )
            slot.result = result
        else:
            # Замок сработал: пока мы стояли в очереди, текст оплатил и
            # получил конкурент. Второго вызова DeepSeek не будет.
            logger.info(
                "Беру текст %s/%s для %s у параллельного запроса — в LLM не идём",
                report_type.value, version, subject,
            )

        # Шаг 4: сохраняем текст ВМЕСТЕ с паспортом генерации (модель,
        # temperature, токены). Токены — прямая себестоимость разбора; модель
        # и temperature нужны, когда на текст пожалуются: без них непонятно,
        # при каких настройках получилась ерунда. А сам корпус текстов —
        # материал для будущего пост-фильтра под модерацию VK.
        report = Report(
            # chart_id заполнен ВСЕГДА, в том числе у парного разбора: на нём
            # держатся каскады, денормализованный user_id и дневной потолок
            # трат. За что заплачено, говорит pair_id.
            chart_id=chart.id,
            pair_id=pair_id,
            user_id=chart.user_id,
            type=report_type,
            prompt_version=version,
            text=result.text,
            model=result.model,
            temperature=temperature,
            tokens_prompt=result.usage.prompt_tokens,
            tokens_completion=result.usage.completion_tokens,
            tokens_cached=result.usage.cached_prompt_tokens,
        )

        # Вставка под SAVEPOINT — тот же приём, что в charts.create_chart().
        # Замок выше защищает только внутри своего процесса, а процессов может
        # быть несколько (api и бот, два воркера). Тогда уникальный адрес
        # отчёта в базе не даст записать второй, и мы спокойно заберём чужой
        # текст. Без savepoint IntegrityError сломал бы всю транзакцию:
        # Postgres перевёл бы её в состояние «отменена», и любой следующий
        # запрос ответил бы «current transaction is aborted» — то есть падала
        # бы вся страница, а не одна вставка.
        try:
            async with session.begin_nested():
                session.add(report)
            return report
        except IntegrityError:
            existing = await _find(
                session,
                chart_id=chart_key,
                pair_id=pair_id,
                report_type=report_type,
                version=version,
            )
            if existing is not None:
                # Нормальный исход, не авария: другой процесс закоммитил тот
                # же отчёт раньше нас. Видеть это в логе полезно — частые
                # такие строки означают, что замок процесса не справляется и
                # генерацию пора выносить в общую очередь задач.
                logger.info(
                    "Отчёт %s/%s для %s записал другой процесс — беру его строку",
                    report_type.value, version, subject,
                )
                return existing
            # Сюда попадаем, если конфликт был не про дубль отчёта (например,
            # карту успели удалить). Глушить нельзя — это ошибка в коде
            # вызывающего, и она должна быть видна.
            raise


def _check_address(
    *, chart: Chart, report_type: ReportType, pair: Pair | None
) -> None:
    """Сверяет тип отчёта с переданным адресом. Ошибка вызывающего — ValueError.

    Три запрета сразу: парный тип без пары (текст не из чего собрать),
    пара при натальном типе (кеш ушёл бы по чужому адресу) и чужая карта
    в якоре — chart обязан быть стороной A, иначе reports.chart_id уедет
    на карту партнёра и вместе с ним каскады и «мои разборы».
    """
    if pair is None:
        if report_type in _PAIR_TYPES:
            raise ValueError(f"Отчёт «{report_type.value}» требует пары")
        return

    if report_type not in _PAIR_TYPES:
        raise ValueError(f"Отчёт «{report_type.value}» не бывает парным")
    if chart.id != pair.chart_a_id:
        raise ValueError("Якорная карта парного отчёта — сторона A пары")
    if pair.calc is None:
        raise ValueError(f"Пара {pair.id} не собрана: синастрия не посчитана")


async def _user_message(
    session: AsyncSession,
    module: ModuleType,
    *,
    chart: Chart,
    pair: Pair | None,
    name: str | None,
) -> str:
    """Собрать сообщение промпту: натальное по карте, парное по паре.

    Контракт парного промпта отличается от натального (build_user(natal,
    name)) и объявлен здесь: build_user(calc, kind=..., name_a=..., name_b=...).
    Имена сторон в промпт идут намеренно — читают текст двое, и без них
    он звучит как «первый и второй человек»; в логи имена не попадают.
    """
    if pair is None:
        # Имя берём из карты — «чья это карта» заполняется в форме; аргумент
        # name перекрывает его, когда имя приходит от платформы (профиль VK).
        return module.build_user(chart.calc["natal"], name or chart.person_name)

    # Карту партнёра тянем через session.get, а не pair.chart_b: связи
    # объявлены lazy="raise_on_sql" и из async-кода бросают исключение.
    chart_b = await session.get(Chart, pair.chart_b_id)
    if chart_b is None:
        raise ValueError(f"Пара {pair.id}: карта стороны B не найдена")

    return module.build_user(
        pair.calc,
        kind=pair.kind.value,
        name_a=name or chart.person_name,
        name_b=chart_b.person_name,
    )


async def _check_allowed(
    session: AsyncSession,
    *,
    user: User,
    chart: Chart,
    report_type: ReportType,
    pair: Pair | None = None,
) -> None:
    """Можно ли этому человеку сгенерировать ещё один отчёт этого типа.

    Проверка стоит ПОСЛЕ замка и после повторной проверки кеша, и это важно:
    уже написанный (то есть уже оплаченный нами) текст человек может
    открывать сколько угодно раз — лимит про деньги, а не про просмотры.
    """
    # Оплачен ли этот разбор — выясняем до всего остального: ответ нужен и
    # потолку, и проверке доступа ниже. Для бесплатных типов в базу не идём,
    # условие обрывается на первой половине.
    paid = report_type in _PAID_TYPES and await orders.has_paid(
        session,
        user_id=user.id,
        # Адрес покупки тот же, что у кеша: за пару платят по pair_id, и
        # заказ на «я + Маша» не должен открывать «я + Лена».
        chart_id=None if pair is not None else chart.id,
        pair_id=pair.id if pair is not None else None,
        report_type=report_type,
    )

    # Дневной потолок считает только неоплаченные генерации. Оплаченная себя
    # окупает с запасом (290 ₽ против 13 копеек за вызов DeepSeek), а отказ
    # после списания денег — это возврат и жалоба. Потолок при этом сработает
    # ровно в день пикового трафика, то есть когда платящих больше всего.
    #
    # Тестеру потолок НЕ снимается: его разборы бесплатны для него и платны
    # для нас, а счёту от DeepSeek всё равно, кто именно потратил.
    if not paid:
        cap = get_settings().daily_generation_limit
        today = await count_generated_last_day(session)
        if today >= cap:
            # WARNING, а не INFO: в отличие от личного лимита это не штатная
            # работа, а остановка сервиса для всех — её надо заметить сегодня,
            # а не найти потом в статистике.
            logger.warning(
                "Дневной потолок генераций выбран: %d из %d за сутки, "
                "отказываю всем, кроме оплативших",
                today, cap,
            )
            raise ReportBudgetError(
                "Сегодня сервис подготовил слишком много разборов — "
                "новые будут доступны завтра"
            )

    # Тестеру открыто всё: он для того и заведён, чтобы вычитывать десятки
    # текстов подряд, включая платный разбор до подключения оплаты.
    if user.is_tester:
        return

    if report_type in _PAID_TYPES and not paid:
        logger.info(
            "Отказ пользователю %s: «%s» для %s не оплачен",
            user.id, report_type.value,
            f"пары {pair.id}" if pair is not None else f"карты {chart.id}",
        )
        raise ReportForbiddenError(f"Разбор «{report_type.value}» ещё не оплачен")

    limit = _limits().get(report_type)
    if limit is None:
        return

    used = await count_generated(session, user.id, report_type)
    if used >= limit:
        # INFO, а не WARNING: это штатная работа лимита, а не сбой. Но видеть
        # её надо — если в лимит упираются все подряд, значит он мал.
        logger.info(
            "Отказ пользователю %s: лимит «%s» исчерпан (%d из %d)",
            user.id, report_type.value, used, limit,
        )
        raise ReportLimitError(
            f"Исчерпан лимит бесплатных разборов «{report_type.value}»: "
            f"{used} из {limit}"
        )


# --- Отзывы ---------------------------------------------------------------

# Потолок длины комментария: поле свободное, и без ограничения в props
# аналитики уедет чья-нибудь вставленная целиком статья.
MAX_COMMENT = 500


async def leave_feedback(
    session: AsyncSession,
    *,
    chart: Chart,
    user: User,
    report_type: ReportType,
    rating: int,
    comment: str = "",
    pair: Pair | None = None,
) -> None:
    """Записать оценку разбора: 1–5 и необязательное «что не так».

    Пишет в `events`, а не в свою таблицу: миграция ради одного поля со
    звёздочками не окупается. Когда отзывов станет много, их место —
    отдельная таблица, которую чистят по запросу человека.

    Не коммитит: транзакцией распоряжается вызывающий. ValueError — оценка
    вне 1–5, это ошибка вызывающего, а не выбор пользователя.
    """
    if not 1 <= rating <= 5:
        raise ValueError(f"Оценка {rating} вне диапазона 1–5")

    # Оценивают конкретную редакцию текста, а не карту: без report_id звёзды
    # после смены VERSION молча переехали бы на новый разбор, которого человек
    # не видел, — и админка сравнивала бы версии по чужим оценкам.
    if pair is not None:
        report = await get_pair_report(session, pair.id, report_type)
    else:
        report = await get_report(session, chart.id, report_type)

    # Правило «в props нет ПДн» здесь держится на самом человеке: он может
    # процитировать в жалобе дату рождения. Проверить это нечем, и потому
    # комментарий не попадает в логи ни на одном слое.
    await events.log(
        session,
        events.FEEDBACK,
        user_id=user.id,
        chart_id=chart.id,
        type=report_type.value,
        report_id=report.id if report is not None else None,
        prompt_version=report.prompt_version if report is not None else None,
        rating=rating,
        comment=comment.strip()[:MAX_COMMENT],
        # Мнение владельца и мнение того, кому переслали ссылку, в отчётах
        # весят по-разному.
        own=chart.user_id == user.id,
    )
