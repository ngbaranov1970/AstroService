"""Схемы слоя карт: данные рождения на вход, карта и расчёт на выход.

Проверка ввода живёт здесь, а не в app/services/charts.py: граница из
CLAUDE.md — сервис знает про наши данные, а не про то, откуда они пришли.
Соседний слой стенда (app/web/forms.py) делает ту же работу для строк
HTML-формы; общие границы (год, длина имени) продублированы намеренно —
стенд уйдёт вместе с Vue-фронтом, и копия останется одна.

Наружу из app/models/ уходит не всё: ни `id`, ни `user_id`, ни
`input_hash` фронту не нужны, а адресом карты служит публичный `token`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Раньше 1900 года города жили по местному солнечному времени, и исторические
# зоны в tzdata становятся приблизительными — а точность расчёта у нас первое
# требование. Та же граница в app/web/forms.py.
MIN_BIRTH_YEAR = 1900

# Столько же, сколько в колонке charts.person_name.
MAX_NAME = 64

# Столько же, сколько в колонке charts.place_name.
MAX_PLACE = 128


class PlaceOut(BaseModel):
    """Подсказка города для поля «место рождения».

    Имени часовой зоны здесь нет намеренно: фронт возвращает координаты, а
    зону сервер определяет заново — см. ChartIn.
    """

    model_config = ConfigDict(from_attributes=True)

    label: str
    lat: float
    lon: float


class ChartIn(BaseModel):
    """Данные рождения из формы Mini App.

    Часовой пояс не принимаем: его сервер считает по координатам, иначе
    правдоподобное чужое имя зоны молча сдвинуло бы карту на часы.
    Координаты и подпись места берутся из выбранной PlaceOut.
    """

    person_name: str | None = Field(default=None, max_length=MAX_NAME)
    birth_date: date
    # None означает «время рождения неизвестно»: отдельный флаг рядом со
    # значением допускал бы противоречие «не знаю, но 17:20». Полдень вместо
    # неизвестного часа подставляет create_chart — одинаково для всех платформ.
    birth_time: time | None = None
    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)
    # Только для показа человеку («тот ли это Ростов»), в расчёт не идёт.
    place_name: str | None = Field(default=None, max_length=MAX_PLACE)

    @field_validator("person_name", "place_name")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        """Пустая строка — это «не подписано», то есть NULL в базе."""
        if value is None:
            return None
        return value.strip() or None

    @field_validator("birth_date")
    @classmethod
    def _sane_birth_date(cls, value: date) -> date:
        if value.year < MIN_BIRTH_YEAR:
            raise ValueError(f"Даты раньше {MIN_BIRTH_YEAR} года не поддерживаются")
        # Сегодня берём по UTC: проверка отсекает опечатки в годе («2091»), а
        # не разбирается, наступило ли уже завтра во Владивостоке.
        if value > datetime.now(UTC).date():
            raise ValueError("Дата рождения в будущем")
        return value


class ChartOut(BaseModel):
    """Карта в списке: чем она подписана и по каким данным посчитана.

    `token` — публичный адрес карты, он же ключ ссылки на оплату; внутренний
    `id` наружу не уходит, по соседнему числу читалась бы чужая дата рождения.
    """

    model_config = ConfigDict(from_attributes=True)

    token: str
    person_name: str | None
    # Локальное время рождения, без зоны — то, что человек сам ввёл.
    birth_local: datetime
    time_known: bool
    place_name: str
    created_at: datetime


class ChartDetail(ChartOut):
    """Карта вместе с расчётом — для экрана карты.

    `calc` отдаётся целиком тем же словарём, что лежит в базе (сейчас ключ
    `natal`, позже добавятся `matrix` и `eastern`): фронту он нужен весь, а
    вторая ручка ради того же JSONB — лишний поход по сети.
    """

    calc: dict
