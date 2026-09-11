"""Фоновые генерации и их состояние: разборы по кнопке и контент сообщества.

Отдельный файл, а не часть маршрутов, по границе «что это вообще такое»:
маршруты — про HTTP (разобрать форму, отдать шаблон, увести редиректом),
а здесь ни одного запроса и ни одного ответа. Здесь работа, которая
начинается ПОСЛЕ того, как ответ уже ушёл браузеру, и живёт своей жизнью
30–60 секунд.

Лежит рядом с сервисами, а не в app/web/, потому что реестр генераций
обязан быть один на процесс: стенд и JSON-API Mini App ходят в одну базу и
к одному отчёту. Два реестра означали бы, что запущенную в Mini App
генерацию стенд не видит и рисует кнопку «получить разбор» заново.

Зачем это вообще нужно, в одном абзаце. LLM отвечает десятки секунд. Если
генерировать прямо в обработчике, страница висит белым экраном, человек
жмёт «обновить» и запускает вторую генерацию — то есть платит дважды.
Поэтому обработчик только ставит задачу и сразу отвечает редиректом, а
страница `/r/<token>` перечитывает себя раз в пять секунд, пока текст не
появится в базе.

Из этого следуют два неочевидных требования, и оба реализованы ниже.

1. У ФОНОВОЙ ЗАДАЧИ СВОЯ СЕССИЯ И СВОИ ИДЕНТИФИКАТОРЫ. Сессия запроса к
   моменту её выполнения уже закрыта, а объекты ORM привязаны к своей
   сессии — передать их в задачу значит получить падение в самом неудобном
   месте.

2. СОСТОЯНИЕ ГЕНЕРАЦИИ ПРИХОДИТСЯ ХРАНИТЬ В ПАМЯТИ. У отчёта намеренно нет
   колонки «статус»: строка появляется сразу с готовым текстом (см. шапку
   app/services/reports.py). Значит по базе «генерация идёт» и «генерация
   упала» неразличимы — а человеку разница видна сразу: в первом случае
   надо ждать, во втором нажать кнопку ещё раз.

Вторым жильцом сюда въехал прогон недели контента (кнопка на /admin/content):
работа той же формы — минуты ожидания, ответ уходит раньше результата, — и
тот же довод про один реестр на процесс.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import BackgroundTasks

from app.db import get_sessionmaker
from app.models import Chart, Pair, ReportType
from app.services import charts, content, events, pairs, reports, users

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Generation:
    """Что происходит с одним отчётом прямо сейчас."""

    running: bool
    # Человеческий текст ошибки — тот, что увидит тестер. Не traceback:
    # подробности уже в логе, а на странице нужна понятная фраза.
    error: str | None = None
    # Тот же отказ машинным кодом: фронту Mini App по нему выбирать, вести
    # ли на оплату или предлагать повтор, а сравнивать текст нельзя — он
    # меняется вместе с формулировками.
    error_code: str | None = None


# Реестр «что сейчас генерируется». Ключ — адрес отчёта (карта или пара) и
# тип: генерации разных разборов друг другу не мешают.
#
# Чем платим за словарь вместо таблицы: состояние теряется при перезапуске
# процесса и не видно второму процессу. Оба случая безобидны — страница
# просто покажет кнопку «получить разбор» вместо «готовим», а повторное
# нажатие ничего не сломает и денег не потратит: готовый текст возьмётся из
# кеша по (chart_id, type, prompt_version). Когда генерация уедет в очередь
# задач, это место заменит статус в базе.
_REGISTRY: dict[tuple[str, str], Generation] = {}


def _subject(chart_id: int, pair_id: int | None) -> str:
    """Кого называет строка лога — пару или карту. Только идентификаторы."""
    return f"пары {pair_id}" if pair_id is not None else f"карты {chart_id}"


def _key(
    report_type: ReportType, *, chart_id: int | None = None, pair_id: int | None = None
) -> tuple[str, str]:
    """Адрес генерации: («c42» или «p7», тип отчёта).

    Префикс c/p обязателен: карты и пары нумеруются независимо, и без него
    разбор пары 7 делил бы запись с натальным разбором карты 7 — страница
    показывала бы «готовим» чужого текста. Тот же приём в замке
    app/services/reports.py.
    """
    owner = f"p{pair_id}" if pair_id is not None else f"c{chart_id}"
    return (owner, report_type.value)


def state(chart_id: int, report_type: ReportType) -> Generation | None:
    """Что известно про генерацию этого отчёта. None — «ничего не происходит».

    Отсутствие записи означает одно из двух: генерацию не запускали или она
    успешно закончилась. Различать их не нужно — в обоих случаях страница
    смотрит в базу, а не сюда.
    """
    return _REGISTRY.get(_key(report_type, chart_id=chart_id))


def pair_state(pair_id: int, report_type: ReportType) -> Generation | None:
    """То же, что state, но для разбора пары."""
    return _REGISTRY.get(_key(report_type, pair_id=pair_id))


def schedule(
    background: BackgroundTasks, chart: Chart, report_type: ReportType
) -> None:
    """Поставить генерацию в очередь и отметить её как идущую.

    Отметка ставится ЗДЕСЬ, а не внутри задачи: задача начнёт выполняться
    только после отправки ответа, а редирект приведёт браузер на страницу
    раньше — и та успела бы увидеть «ничего не происходит» и показать
    кнопку вместо «готовим».

    Генерация всегда считается от имени ВЛАДЕЛЬЦА карты (chart.user_id), а
    не того, кто нажал кнопку: лимиты бесплатных разборов и оплата суть
    свойства владельца.

    BackgroundTasks приходит аргументом от вызывающего маршрута. Объект
    FastAPI здесь допустим — модуль не в app/services/, где запрет на
    объекты фреймворка нужен ради бота; бот эту очередь не использует.
    """
    _schedule(
        background,
        chart_id=chart.id,
        user_id=chart.user_id,
        report_type=report_type,
        pair_id=None,
    )


def schedule_pair(
    background: BackgroundTasks, pair: Pair, report_type: ReportType
) -> None:
    """То же для разбора совместимости.

    Карта в аргументах не нужна: якорь (сторона A) и владелец лежат в самой
    паре. Отдельная функция, а не необязательный аргумент у schedule, по той
    же причине, что get_pair_report в services/reports.py — адрес ровно один
    из двух, и общая сигнатура позволила бы позвать её без адреса вовсе.
    """
    _schedule(
        background,
        chart_id=pair.chart_a_id,
        user_id=pair.user_id,
        report_type=report_type,
        pair_id=pair.id,
    )


def _schedule(
    background: BackgroundTasks,
    *,
    chart_id: int,
    user_id: int,
    report_type: ReportType,
    pair_id: int | None,
) -> None:
    """Общая часть обеих постановок: отметка «идёт» и сама задача.

    Одной функцией, чтобы отметка и задача не разъехались между двумя
    копиями: запись в реестр без задачи даёт вечное «готовим».
    """
    _REGISTRY[_key(report_type, chart_id=chart_id, pair_id=pair_id)] = Generation(
        running=True
    )
    background.add_task(_run, chart_id, user_id, report_type, pair_id)


async def _run(
    chart_id: int,
    user_id: int,
    report_type: ReportType,
    pair_id: int | None = None,
) -> None:
    """Сходить в DeepSeek и записать отчёт. Выполняется после ответа браузеру.

    Ловим здесь ВСЁ, и это не перестраховка: исключение из фоновой задачи
    не дойдёт ни до кого — ни до человека (ответ давно отправлен), ни до
    обработчика (его уже нет). Молча потерянная ошибка оставила бы тестера
    перед вечным «готовим разбор».

    Аргументы — идентификаторы, а не объекты Chart и User: см. пункт 1
    в шапке файла.
    """
    key = _key(report_type, chart_id=chart_id, pair_id=pair_id)

    # Своя сессия и своя транзакция: с запросом у этой работы уже нет
    # ничего общего.
    async with get_sessionmaker()() as session:
        try:
            chart = await charts.get_chart(session, chart_id)
            user = await users.get_by_id(session, user_id)
            if chart is None or user is None:
                # Карту или пользователя удалили, пока задача стояла в очереди.
                logger.warning(
                    "Фоновая генерация отменена: карта %s или пользователь %s исчезли",
                    chart_id, user_id,
                )
                _REGISTRY.pop(key, None)
                return

            pair = None
            if pair_id is not None:
                pair = await pairs.get_pair(session, pair_id)
                if pair is None:
                    # Пара уходит каскадом вместе с любой своей картой —
                    # партнёра могли удалить, пока задача ждала очереди.
                    logger.warning(
                        "Фоновая генерация отменена: пара %s исчезла", pair_id
                    )
                    _REGISTRY.pop(key, None)
                    return

            await reports.get_or_create_report(
                session, chart=chart, user=user, report_type=report_type, pair=pair
            )
            await events.log(
                session, events.REPORT_READY,
                user_id=user_id, chart_id=chart_id, type=report_type.value,
                pair_id=pair_id,
            )
            await session.commit()
            # Успех: убираем отметку. Дальше страница смотрит в базу.
            _REGISTRY.pop(key, None)

        except reports.ReportError as exc:
            # Штатный отказ: кончился лимит, отчёт только для тестеров, нет
            # промпта под этот тип. Не авария — у интерфейса на это есть
            # человеческий текст.
            await session.rollback()
            logger.info(
                "Отказ в генерации %s для %s: %s",
                report_type.value, _subject(chart_id, pair_id), exc,
            )
            code, text = _failure(exc)
            _REGISTRY[key] = Generation(running=False, error=text, error_code=code)
            await events.log(
                session, events.REPORT_DENIED,
                user_id=user_id, chart_id=chart_id, type=report_type.value,
                pair_id=pair_id, reason=type(exc).__name__,
            )
            await session.commit()

        except Exception:
            # Авария: DeepSeek не ответил, база отвалилась, ошибка в коде.
            # exc_info в лог обязателен — это единственный след случившегося.
            await session.rollback()
            logger.exception(
                "Фоновая генерация %s для %s провалилась",
                report_type.value, _subject(chart_id, pair_id),
            )
            _REGISTRY[key] = Generation(
                running=False,
                error="Не получилось получить текст. Попробуйте ещё раз.",
                error_code=FAILED,
            )
            await events.log(
                session, events.REPORT_FAILED,
                user_id=user_id, chart_id=chart_id, type=report_type.value,
                pair_id=pair_id,
            )
            await session.commit()


# Коды отказов для JSON-API. Строки, а не Enum: они уезжают в ответ как есть,
# и лишний слой преобразования тут ничего не даёт.
LIMIT_EXHAUSTED = "limit_exhausted"
NEEDS_PURCHASE = "needs_purchase"
BUDGET_EXCEEDED = "budget_exceeded"
NOT_SUPPORTED = "not_supported"
FAILED = "failed"

# Отказ в двух видах сразу: код для фронта, фраза для страницы стенда.
# Одна таблица вместо двух функций — разъехавшись, они сказали бы разное про
# один и тот же отказ.
_FAILURES: tuple[tuple[type[reports.ReportError], str, str], ...] = (
    (reports.ReportLimitError, LIMIT_EXHAUSTED, "Бесплатные разборы закончились."),
    # Про тестеров человеку не рассказываем: он сюда попадает, только отправив
    # форму в обход анонса, и «вы не тестер» ему ничего не объясняет.
    # Модератор каталога VK увидит то же самое.
    (
        reports.ReportForbiddenError,
        NEEDS_PURCHASE,
        "Полный разбор станет доступен, когда заработает оплата.",
    ),
    # Про потолок и деньги человеку не рассказываем: он ни при чём, ему нужен
    # срок. Текст самой ошибки в интерфейс не отдаём намеренно.
    (
        reports.ReportBudgetError,
        BUDGET_EXCEEDED,
        "Сегодня разборы закончились. Возвращайтесь завтра.",
    ),
)


def _failure(exc: reports.ReportError) -> tuple[str, str]:
    """Код и человеческий текст отказа. Формулировки — часть продукта, а не логов."""
    for error_type, code, text in _FAILURES:
        if isinstance(exc, error_type):
            return code, text
    # Сюда попадают ReportNotSupportedError и любой будущий наследник
    # ReportError: пообещать человеку разбор, которого нет, хуже, чем сказать
    # «ещё не готов».
    return NOT_SUPPORTED, "Этот вид разбора ещё не готов."


# --- Контент сообщества ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class ContentRun:
    """Прогон недели контента: идёт ли он и чем кончился прошлый.

    Времена в UTC — их печатает шаблон фильтром `| msk`.
    """

    running: bool
    started_at: datetime
    finished_at: datetime | None = None
    # Сколько постов стоит на неделе после прогона — вместе с написанными
    # раньше: generate_week возвращает всю пачку, а не только новое.
    posts: int = 0
    error: str | None = None


# Прогон один на процесс, поэтому ячейка, а не словарь: контент ничей, и
# ключевать его нечем — «вторая неделя параллельно первой» смысла не имеет.
_CONTENT_RUN: ContentRun | None = None


def content_state() -> ContentRun | None:
    """Что известно про прогон недели. None — в этом процессе не запускали."""
    return _CONTENT_RUN


def schedule_content(background: BackgroundTasks) -> bool:
    """Поставить прогон недели в очередь. False — прогон уже идёт, второй не ставим.

    Это и есть замок от двойного нажатия: страница ждёт минут десять, за это
    время кнопку нажмут ещё раз, а второй прогон — второй счёт от DeepSeek
    (кеш спасает только уже дописанные посты, идущие сейчас в базе ещё нет).

    Проверка и запись идут без `await` между собой, поэтому двум запросам
    разъехаться негде: asyncio не прерывает корутину посередине.
    """
    global _CONTENT_RUN
    if _CONTENT_RUN is not None and _CONTENT_RUN.running:
        return False

    _CONTENT_RUN = ContentRun(running=True, started_at=datetime.now(UTC))
    background.add_task(_run_content)
    return True


async def _run_content() -> None:
    """Прогнать неделю контента. Выполняется после ответа браузеру, минут десять.

    Сюда долетают только аварии уровня «эфемериды не считаются» или «база
    отвалилась»: ошибку отдельного поста generate_week гасит сама и идёт
    дальше, иначе падение на девятом вызове выбросило бы восемь оплаченных
    текстов.
    """
    global _CONTENT_RUN
    # Запись уже стоит — её поставил schedule_content до ответа браузеру.
    started = _CONTENT_RUN.started_at if _CONTENT_RUN else datetime.now(UTC)

    async with get_sessionmaker()() as session:
        try:
            posts = await content.generate_week(session)
            _CONTENT_RUN = ContentRun(
                running=False,
                started_at=started,
                finished_at=datetime.now(UTC),
                posts=len(posts),
            )
        except Exception:
            await session.rollback()
            # exc_info обязателен: страница скажет только «не дошёл до конца»,
            # а больше следа не останется — ответ давно отправлен.
            logger.exception("Прогон недели контента провалился")
            _CONTENT_RUN = ContentRun(
                running=False,
                started_at=started,
                finished_at=datetime.now(UTC),
                error="Прогон не дошёл до конца — смотрите лог.",
            )
