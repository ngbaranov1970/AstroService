"""JSON-маршруты слоя карт: подсказки городов, расчёт, чтение.

Слой тонкий по замыслу: принять схему, позвать app/services/charts.py,
отдать схему. Ни расчётов, ни SQL здесь нет — они в сервисах, и это
единственный вход в них после сноса стенда.

Генерации разборов здесь нет намеренно: её ставит app/api/routes/reports.py
отдельным запросом. Фронт получает карту сразу (расчёт занимает ~100 мс) и
сам решает, какой текст просить.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import ChartByToken, CurrentUser, SessionDep
from app.schemas.charts import ChartDetail, ChartIn, ChartOut, PlaceOut
from app.services import charts as charts_service
from app.services import events

router = APIRouter(tags=["charts"])

# Ограничение на строку поиска: справочник ищет по префиксу, и длинный запрос
# всё равно ничего не найдёт, а в лог и метрики уедет чужой мусор.
MAX_QUERY = 64


@router.get("/places", response_model=list[PlaceOut])
async def search_places(
    _user: CurrentUser,
    q: Annotated[str, Query(max_length=MAX_QUERY)],
) -> list[PlaceOut]:
    """Подсказки городов для поля «место рождения», крупные — первыми.

    Пустой список означает «не нашли» (в том числе на запрос короче двух
    символов), это не ошибка. В базу не ходит: справочник лежит в памяти
    процесса, поэтому сервис синхронный.

    Токен требуется, хотя личных данных в ответе нет: справочник — наш
    ресурс, и открытая ручка сделала бы из него бесплатный чужой геокодер.
    """
    found = charts_service.search_places(q)
    return [PlaceOut.model_validate(place) for place in found]


@router.get("/charts", response_model=list[ChartOut])
async def list_charts(user: CurrentUser, session: SessionDep) -> list[ChartOut]:
    """Карты этого пользователя, свежие первыми."""
    rows = await charts_service.list_charts(session, user.id)
    return [ChartOut.model_validate(chart) for chart in rows]


@router.post("/charts", response_model=ChartOut, status_code=status.HTTP_201_CREATED)
async def create_chart(
    payload: ChartIn, user: CurrentUser, session: SessionDep
) -> ChartOut:
    """Посчитать карту по данным рождения и записать её.

    Повтор тех же данных отдаёт уже посчитанную карту, а не вторую такую же
    (дедупликация по input_hash в сервисе). Коммитит сам: карту и событие
    иначе откатит закрытие сессии.
    """
    # Полночь — заглушка на время сборки datetime: при time_known=False её
    # заменит полдень внутри create_chart, одинаково для всех платформ.
    birth_local = datetime.combine(payload.birth_date, payload.birth_time or time.min)
    time_known = payload.birth_time is not None

    # Таймзону определяем по координатам, а не принимаем от клиента:
    # правдоподобное чужое имя зоны молча сдвинуло бы карту на часы.
    place = charts_service.place_from_coords(
        payload.lat, payload.lon, payload.place_name
    )

    chart = await charts_service.create_chart(
        session,
        user_id=user.id,
        birth_local=birth_local,
        place=place,
        time_known=time_known,
    )
    # Имя проставляем и на повторе той же карты: человек мог завести её без
    # подписи, а потом вернуться и подписать.
    if payload.person_name and chart.person_name != payload.person_name:
        chart.person_name = payload.person_name

    await events.log(
        session,
        events.CHART_CREATED,
        user_id=user.id,
        chart_id=chart.id,
        time_known=time_known,
    )
    await session.commit()

    return ChartOut.model_validate(chart)


@router.get("/charts/{token}", response_model=ChartDetail)
async def get_chart(chart: ChartByToken) -> ChartDetail:
    """Карта вместе с расчётом — всё, что нужно экрану карты.

    Авторизацию требует сама зависимость ChartByToken, поэтому отдельного
    аргумента под пользователя здесь нет. Проверки владельца нет тоже: токен
    карты и есть ключ, 24 случайных байта не подобрать перебором. Следствие
    осознанное — кто знает ссылку, тот видит карту; платный текст закрывает
    не этот маршрут, а reports.
    """
    return ChartDetail.model_validate(chart)
