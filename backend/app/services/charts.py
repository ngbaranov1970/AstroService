"""Карты: поиск города, расчёт, сохранение в базу.

Склейка расчётного ядра (astro_core умеет только «дата + координаты →
словарь») и моделей (app/models/ не знают, как этот словарь получить).

Про Telegram, VK и HTTP здесь нет ни слова: те же функции вызывают бот,
веб-стенд и Mini App — это граница из CLAUDE.md.

Почему поиск города отделён от расчёта и почему дубли ловятся по input_hash —
см. docs/astro-context.md, раздел «Решения по слою карт».
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import datetime, time
from importlib.metadata import version

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chart, User
from astro_core.geo import City, search_cities, tz_at
from astro_core.natal import compute_natal

# Пишется в Chart.calc_version: по нему видно, какие карты пересчитать после
# обновления kerykeion/pyswisseph (сдвиг до 1' дуги). Номера читаем из
# установленных пакетов: вписанные строкой не изменятся после uv sync, новые
# карты уйдут в базу под старой версией и пересчёт отберёт не те. "n1" —
# версия формата словаря calc, меняется руками при смене полей в compute_natal.
CALC_VERSION = f"n1-k{version('kerykeion')}-s{version('pyswisseph')}"

# Подставляем, когда час рождения неизвестен. Полдень, а не полночь: дома,
# Асцендент и MC обходят весь круг за сутки, и полдень как середина диапазона
# держит промах в пределах 12 часов вместо 24. Полночь вдобавок при переводе
# в UTC уезжает на соседнюю дату, а вместе с датой уезжает Луна (~13° в сутки).
UNKNOWN_TIME = time(12, 0)


@dataclass(frozen=True, slots=True)
class Place:
    """Место рождения, готовое к показу и к расчёту.

    Не astro_core.geo.City: наружу уходит только нужное интерфейсу, и место
    может прийти точкой на карте (place_from_coords). label — подпись со
    страной («Ростов-на-Дону, RU»), иначе одинаковые названия не различить.
    """

    label: str
    lat: float
    lon: float
    tz: str


def _place_from_city(city: City) -> Place:
    return Place(
        label=f"{city.display_name}, {city.country}",
        lat=city.lat,
        lon=city.lon,
        tz=city.tz,
    )


def search_places(query: str, limit: int = 8) -> list[Place]:
    """Подсказки для поля «город рождения», крупные города — первыми.

    Пустой список означает «ничего не нашли» (в том числе на запрос короче
    двух символов), это не ошибка. Функция синхронная и в базу не ходит:
    справочник городов целиком лежит в памяти процесса.
    """
    return [_place_from_city(c) for c in search_cities(query, limit)]


def place_from_coords(lat: float, lon: float, label: str | None = None) -> Place:
    """Место по координатам: точка на карте или село, которого нет
    в справочнике (там только города от 5000 жителей).

    Таймзону определяем геометрически (timezonefinder) и берём именно имя
    зоны: смещение зависит от даты рождения и вычисляется позже, в расчёте.
    """
    return Place(
        label=label or f"{lat:.4f}, {lon:.4f}",
        lat=lat,
        lon=lon,
        tz=tz_at(lat, lon),
    )


def _input_hash(
    birth_local: datetime, lat: float, lon: float, tz: str, time_known: bool
) -> str:
    """Отпечаток входных данных: одинаковый вход — одинаковая строка.

    Что входит и почему — docs/astro-context.md, «Решения по слою карт».
    Подписи места в хеше нет: расчёт от неё не зависит.
    """
    parts = "|".join(
        [
            birth_local.isoformat(timespec="minutes"),
            "t" if time_known else "f",
            # 4 знака (~11 м) — не про точность, а про стабильность хеша.
            f"{lat:.4f}",
            f"{lon:.4f}",
            tz,
        ]
    )
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()


async def _find_by_hash(
    session: AsyncSession, user_id: int, input_hash: str
) -> Chart | None:
    stmt = select(Chart).where(
        Chart.user_id == user_id, Chart.input_hash == input_hash
    )
    return await session.scalar(stmt)


async def create_chart(
    session: AsyncSession,
    *,
    user_id: int,
    birth_local: datetime,
    place: Place,
    time_known: bool = True,
) -> Chart:
    """Главная функция модуля: данные рождения → строка в charts.

    Возвращает созданную карту либо ту, что уже была, если этот пользователь
    считал ровно эти данные раньше (отличить можно по chart.created_at).
    Транзакцию не коммитит — это делает вызывающий, см. get_session в app/db.py.
    """
    # Полдень подставляем здесь, до вычисления хеша: оставь это вызывающему —
    # бот подставит 12:00, веб 00:00, и одна карта запишется дважды.
    if not time_known:
        birth_local = datetime.combine(birth_local.date(), UNKNOWN_TIME)
    else:
        # Секунды и микросекунды — мусор от datetime.now() и парсеров формы,
        # но в хеш они попадут: «17:20» разойдётся с «17:20:00.000123».
        birth_local = birth_local.replace(second=0, microsecond=0)

    input_hash = _input_hash(
        birth_local, place.lat, place.lon, place.tz, time_known
    )

    # Шаг 1: может, уже считали. До расчёта, а не после: он стоит ~100 мс.
    existing = await _find_by_hash(session, user_id, input_hash)
    if existing is not None:
        return existing

    # Шаг 2: считаем. compute_natal синхронный и занимает процессор на 50–150 мс
    # (эфемериды); вызванный прямо в event loop, он на это время замораживает
    # весь процесс, поэтому уводим его в отдельный поток.
    natal = await asyncio.to_thread(
        compute_natal, birth_local, place.lat, place.lon, place.tz
    )

    chart = Chart(
        user_id=user_id,
        birth_local=birth_local,
        time_known=time_known,
        # Обрезаем под String(128): подпись приходит снаружи и не должна ронять вставку.
        place_name=place.label[:128],
        lat=place.lat,
        lon=place.lon,
        tz=place.tz,
        input_hash=input_hash,
        # Контейнер под все расчёты по этим данным: matrix и eastern лягут
        # соседними ключами, новой таблицы не потребуется.
        calc={"natal": natal},
        calc_version=CALC_VERSION,
    )

    # Шаг 3: вставка с защитой от гонки. Пока мы считали карту, тот же человек
    # мог отправить форму второй раз — оба запроса не нашли дубль и оба вставляют.
    # Уникальная пара (user_id, input_hash) не даст записать второй.
    try:
        # begin_nested() — это SAVEPOINT. Без него неудачная вставка ломает всю
        # транзакцию: Postgres ответит «current transaction is aborted» на любой
        # следующий запрос. С савпоинтом откатывается только сама вставка.
        async with session.begin_nested():
            session.add(chart)
        # Выход из savepoint делает flush, то есть chart.id уже настоящий.
        await _set_primary_if_first(session, user_id, chart.id)
        return chart
    except IntegrityError:
        existing = await _find_by_hash(session, user_id, input_hash)
        if existing is not None:
            return existing
        # Ошибка не про дубль карты (например, user_id несуществующий) —
        # глушить нельзя, это ошибка в коде вызывающего.
        raise


async def _set_primary_if_first(
    session: AsyncSession, user_id: int, chart_id: int
) -> None:
    """Первую карту человека отметить как его собственную.

    Условие «признак пуст» стоит в самом UPDATE, а не в коде: база проверяет
    и пишет одной командой, и влезть между проверкой и записью нельзя.

    Вторая и третья карты признак НЕ перебивают — иначе расчёт карты подруги
    молча переключал бы человеку его собственный гороскоп в рассылке.
    """
    await session.execute(
        update(User)
        .where(User.id == user_id, User.primary_chart_id.is_(None))
        .values(primary_chart_id=chart_id)
    )


async def get_chart(
    session: AsyncSession, chart_id: int, *, user_id: int | None = None
) -> Chart | None:
    """Карта по идентификатору, None — если её нет.

    user_id — проверка владения, передавать всегда, когда идентификатор пришёл
    снаружи (URL, callback-кнопка): иначе человек сменит /chart/41 на /chart/42
    и прочитает чужую дату рождения. Без него — только там, где владелец
    проверен выше или неважен.
    """
    stmt = select(Chart).where(Chart.id == chart_id)
    if user_id is not None:
        stmt = stmt.where(Chart.user_id == user_id)
    return await session.scalar(stmt)


async def get_chart_by_token(session: AsyncSession, token: str) -> Chart | None:
    """Карта по публичному токену из ссылки /r/<token>, None — если такой нет.

    Проверки владения нет намеренно: сам токен и есть ключ, 24 случайных байта
    не подобрать перебором (см. Chart.token в models/chart.py). Следствие: кто знает
    ссылку — видит разбор; на платные разделы добавится проверка владельца.
    """
    return await session.scalar(select(Chart).where(Chart.token == token))


async def list_charts(session: AsyncSession, user_id: int) -> list[Chart]:
    """Все карты пользователя, свежие первыми.

    Постраничности и лимита нет: карт обычно одна-две («своя» и «мужа»).
    """
    stmt = (
        select(Chart)
        .where(Chart.user_id == user_id)
        .order_by(Chart.created_at.desc())
    )
    return list(await session.scalars(stmt))
