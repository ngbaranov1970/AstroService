"""Фоновая генерация разборов и её состояние.

Отдельный файл, а не часть router.py, по границе «что это вообще такое»:
маршруты — про HTTP (разобрать форму, отдать шаблон, увести редиректом),
а здесь ни одного запроса и ни одного ответа. Здесь работа, которая
начинается ПОСЛЕ того, как ответ уже ушёл браузеру, и живёт своей жизнью
30–60 секунд.

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
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import BackgroundTasks

from app.db import get_sessionmaker
from app.models import Chart, ReportType
from app.services import charts, events, reports, users

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Generation:
    """Что происходит с одним отчётом прямо сейчас."""

    running: bool
    # Человеческий текст ошибки — тот, что увидит тестер. Не traceback:
    # подробности уже в логе, а на странице нужна понятная фраза.
    error: str | None = None


# Реестр «что сейчас генерируется». Ключ — карта и тип отчёта: генерации
# разных разборов друг другу не мешают.
#
# Чем платим за словарь вместо таблицы: состояние теряется при перезапуске
# процесса и не видно второму процессу. Оба случая безобидны — страница
# просто покажет кнопку «получить разбор» вместо «готовим», а повторное
# нажатие ничего не сломает и денег не потратит: готовый текст возьмётся из
# кеша по (chart_id, type, prompt_version). Когда генерация уедет в очередь
# задач, это место заменит статус в базе.
_REGISTRY: dict[tuple[int, str], Generation] = {}


def _key(chart_id: int, report_type: ReportType) -> tuple[int, str]:
    return (chart_id, report_type.value)


def state(chart_id: int, report_type: ReportType) -> Generation | None:
    """Что известно про генерацию этого отчёта. None — «ничего не происходит».

    Отсутствие записи означает одно из двух: генерацию не запускали или она
    успешно закончилась. Различать их не нужно — в обоих случаях страница
    смотрит в базу, а не сюда.
    """
    return _REGISTRY.get(_key(chart_id, report_type))


def schedule(
    background: BackgroundTasks, chart: Chart, report_type: ReportType
) -> None:
    """Поставить генерацию в очередь и отметить её как идущую.

    Отметка ставится ЗДЕСЬ, а не внутри задачи: задача начнёт выполняться
    только после отправки ответа, а редирект приведёт браузер на страницу
    раньше — и та успела бы увидеть «ничего не происходит» и показать
    кнопку вместо «готовим».

    Генерация всегда считается от имени ВЛАДЕЛЬЦА карты (chart.user_id), а
    не того, кто нажал кнопку: лимиты бесплатных разборов и — позже —
    оплата суть свойства владельца.

    BackgroundTasks — объект FastAPI, и это нормально: файл лежит в
    app/web/. Запрет на объекты фреймворка действует для app/services/*,
    которые обслуживают ещё и бота.
    """
    _REGISTRY[_key(chart.id, report_type)] = Generation(running=True)
    background.add_task(_run, chart.id, chart.user_id, report_type)


async def _run(chart_id: int, user_id: int, report_type: ReportType) -> None:
    """Сходить в DeepSeek и записать отчёт. Выполняется после ответа браузеру.

    Ловим здесь ВСЁ, и это не перестраховка: исключение из фоновой задачи
    не дойдёт ни до кого — ни до человека (ответ давно отправлен), ни до
    обработчика (его уже нет). Молча потерянная ошибка оставила бы тестера
    перед вечным «готовим разбор».

    Аргументы — идентификаторы, а не объекты Chart и User: см. пункт 1
    в шапке файла.
    """
    key = _key(chart_id, report_type)

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

            await reports.get_or_create_report(
                session, chart=chart, user=user, report_type=report_type
            )
            await events.log(
                session, events.REPORT_READY,
                user_id=user_id, chart_id=chart_id, type=report_type.value,
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
                "Отказ в генерации %s для карты %s: %s",
                report_type.value, chart_id, exc,
            )
            _REGISTRY[key] = Generation(running=False, error=_human_error(exc))
            await events.log(
                session, events.REPORT_DENIED,
                user_id=user_id, chart_id=chart_id, type=report_type.value,
                reason=type(exc).__name__,
            )
            await session.commit()

        except Exception:
            # Авария: DeepSeek не ответил, база отвалилась, ошибка в коде.
            # exc_info в лог обязателен — это единственный след случившегося.
            await session.rollback()
            logger.exception(
                "Фоновая генерация %s для карты %s провалилась",
                report_type.value, chart_id,
            )
            _REGISTRY[key] = Generation(
                running=False,
                error="Не получилось получить текст. Попробуйте ещё раз.",
            )
            await events.log(
                session, events.REPORT_FAILED,
                user_id=user_id, chart_id=chart_id, type=report_type.value,
            )
            await session.commit()


def _human_error(exc: reports.ReportError) -> str:
    """Текст отказа для страницы. Формулировки — часть продукта, а не логов."""
    if isinstance(exc, reports.ReportLimitError):
        return "Бесплатные разборы закончились."
    if isinstance(exc, reports.ReportForbiddenError):
        # Про тестеров человеку не рассказываем: он сюда попадает, только
        # отправив форму в обход анонса, и «вы не тестер» ему ничего не
        # объясняет. Модератор каталога VK увидит то же самое.
        return "Полный разбор станет доступен, когда заработает оплата."
    if isinstance(exc, reports.ReportBudgetError):
        # Про потолок и деньги человеку не рассказываем: он ни при чём, ему
        # нужен срок. Текст самой ошибки в интерфейс не отдаём намеренно.
        return "Сегодня разборы закончились. Возвращайтесь завтра."
    return "Этот вид разбора ещё не готов."
