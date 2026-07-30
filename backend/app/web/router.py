"""Веб-стенд для обкатки промптов: HTML прямо из FastAPI, без сборки и npm.

Это инструмент, а не продукт. Его единственная задача — дать возможность
положить два разбора разных людей рядом и увидеть, привязывается ли DeepSeek
к конкретной карте или льёт воду. Ни Vue, ни VKUI здесь не будет: настоящий
фронт верстается по устоявшемуся контенту, а контент как раз и не устоялся.

Слой тонкий по замыслу: принять запрос, позвать сервис, отдать шаблон.
Ни расчётов, ни SQL, ни знания о том, какой промпт что возвращает, здесь
нет — иначе бот и Mini App, которые будут звать те же сервисы, получат
другое поведение.

Файл содержит ТОЛЬКО маршруты. Соседи по папке взяли на себя то, что к HTTP
отношения не имеет:

* `deps.py` — кто пришёл (cookie → пользователь) и охрана админки;
* `forms.py` — проверка того, что человек ввёл в поля;
* `generation.py` — фоновая генерация текстов и её состояние.

Три решения, без которых стенд не работает. Все — следствия одного факта:
LLM отвечает 30–60 секунд.

1. ГЕНЕРАЦИЮ ЗАПУСКАЕТ ФОНОВАЯ ЗАДАЧА, А НЕ ОБРАБОТЧИК (подробности — в
   generation.py). Иначе `GET /r/...` держал бы ответ минуту: белый экран,
   человек жмёт «обновить» и запускает вторую генерацию, то есть платит
   дважды.
2. POST ОТВЕЧАЕТ РЕДИРЕКТОМ (303). Страница `/r/<token>` дальше сама
   перечитывает себя через `<meta http-equiv="refresh">`, пока текст не
   появится. JS не нужен вовсе.
3. GET ТОЛЬКО ЧИТАЕТ БАЗУ. Есть строка отчёта — показываем текст, нет —
   «готовим разбор».
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from app.db import get_session
from app.models import Chart, ReportType, User
from app.services import charts, events, reports
from app.web import forms, generation
from app.web.deps import TESTER_QUERY_PARAM, current_user

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

# Объект шаблонов один на процесс: внутри него кеш скомпилированных шаблонов.
# Автоэкранирование для .html включено по умолчанию — всё, что человек ввёл
# в форму (имя, название города), попадает в страницу безопасно.
templates = Jinja2Templates(directory=TEMPLATES_DIR)

router = APIRouter(
    # Стенд в OpenAPI не нужен: /docs описывает JSON-API для Mini App,
    # а тут HTML-страницы для людей.
    include_in_schema=False,
)

# Через сколько секунд страница перечитывает себя, пока текст готовится.
# Пять — компромисс: реже раздражает ожиданием, чаще создаёт лишние запросы
# (каждый — это SELECT, и он попадает в лог доступа).
REFRESH_SECONDS = 5

# Потолок длины комментария в отзыве. Поле свободное, и без ограничения
# в JSONB аналитики уедет чья-нибудь вставленная целиком статья.
MAX_COMMENT = 500


# --- Страницы --------------------------------------------------------------

# Тип-сокращение: и сессия, и пользователь нужны почти каждому обработчику.
SessionDep = Annotated[AsyncSession, Depends(get_session)]
UserDep = Annotated[User, Depends(current_user)]


@router.get("/")
async def index(request: Request, user: UserDep, session: SessionDep) -> Response:
    """Форма ввода данных рождения и список уже посчитанных карт."""
    # Секретную ссылку /?t=<секрет> уводим с глаз: работу свою она уже
    # сделала (тестер проставлен в current_user), а дальше секрет только
    # мешает — он останется в истории браузера и уедет в заголовке Referer
    # на любой внешний сайт, если тестер куда-то перейдёт.
    if request.query_params.get(TESTER_QUERY_PARAM):
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)

    my_charts = await charts.list_charts(session, user.id)
    await events.log(session, events.FORM_OPENED, user_id=user.id)
    await session.commit()

    return templates.TemplateResponse(
        request,
        "index.html",
        {"user": user, "charts": my_charts},
    )


@router.post("/places")
async def choose_place(
    request: Request,
    user: UserDep,
    session: SessionDep,
    person_name: Annotated[str, Form()] = "",
    birth_date: Annotated[str, Form()] = "",
    birth_time: Annotated[str, Form()] = "",
    time_known: Annotated[bool, Form()] = False,
    city: Annotated[str, Form()] = "",
) -> Response:
    """Второй шаг: показать, какие города нашлись по введённому названию.

    Отдельный шаг существует ровно потому, что «Ростов» — это два разных
    города в тысяче километров друг от друга, и выбрать между ними может
    только человек. Данные первого шага (имя, дата, время) едут через
    скрытые поля формы: складывать их в сессию на сервере ради одного
    перехода — лишняя сущность.

    Поиск синхронный и в базу не ходит: справочник городов лежит в памяти
    процесса, двоичный поиск занимает доли миллисекунды.
    """
    try:
        birth_local = forms.parse_birth(birth_date, birth_time, time_known)
    except forms.FormError as exc:
        return await _index_with_error(request, user, session, str(exc))

    found = charts.search_places(city)
    if not found:
        return await _index_with_error(
            request, user, session,
            "Город не найден. Попробуйте другое написание или ближайший крупный город.",
        )

    return templates.TemplateResponse(
        request,
        "places.html",
        {
            "user": user,
            "places": found,
            "person_name": forms.clean_name(person_name) or "",
            "birth_date": birth_date,
            "birth_time": birth_time,
            "time_known": time_known,
            # Время в человеческом виде — чтобы на шаге выбора города было
            # видно, что именно сейчас будет посчитано.
            "birth_local": birth_local,
        },
    )


@router.post("/chart")
async def create_chart(
    request: Request,
    user: UserDep,
    session: SessionDep,
    background: BackgroundTasks,
    person_name: Annotated[str, Form()] = "",
    birth_date: Annotated[str, Form()] = "",
    birth_time: Annotated[str, Form()] = "",
    time_known: Annotated[bool, Form()] = False,
    lat: Annotated[float, Form()] = 0.0,
    lon: Annotated[float, Form()] = 0.0,
    place_label: Annotated[str, Form()] = "",
) -> Response:
    """Третий шаг: город выбран — считаем карту и уходим на её страницу."""
    try:
        birth_local = forms.parse_birth(birth_date, birth_time, time_known)
        lat, lon = forms.parse_coords(lat, lon)
    except forms.FormError as exc:
        return await _index_with_error(request, user, session, str(exc))

    # Таймзону определяем ЗАНОВО по координатам, а не берём из скрытого поля
    # формы. Всё, что пришло от клиента, — это предложение, а не факт:
    # подделанное имя зоны («Europe/Nowhere») уронило бы расчёт, а
    # правдоподобное, но чужое — молча сдвинуло бы всю карту на часы.
    place = charts.place_from_coords(lat, lon, place_label.strip() or None)

    chart = await charts.create_chart(
        session,
        user_id=user.id,
        birth_local=birth_local,
        place=place,
        time_known=time_known,
    )
    # Имя проставляем и на повторной отправке той же карты: человек мог
    # завести её без подписи, а потом вернуться и подписать.
    name = forms.clean_name(person_name)
    if name and chart.person_name != name:
        chart.person_name = name

    await events.log(
        session, events.CHART_CREATED,
        user_id=user.id, chart_id=chart.id, time_known=time_known,
    )
    await session.commit()

    # Бесплатный разбор запускаем сразу: он и есть приманка, ждать от
    # человека ещё одного нажатия незачем. Полный — только по кнопке, иначе
    # каждая карта стоила бы вдвое, включая те, до которых не долистали.
    if await reports.get_report(session, chart.id, ReportType.FREE_NATAL) is None:
        generation.schedule(background, chart, ReportType.FREE_NATAL)

    # 303, а не 302: после POST браузер обязан перейти на новый адрес именно
    # методом GET. Тогда «обновить страницу» перечитывает разбор, а не
    # переотправляет форму.
    return RedirectResponse(f"/r/{chart.token}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/r/{token}")
async def report_page(
    request: Request, token: str, user: UserDep, session: SessionDep
) -> Response:
    """Страница карты: расчёт готов сразу, тексты догружаются.

    Обработчик ТОЛЬКО читает. Если начать генерировать здесь, ответ будет
    висеть минуту, и обновление страницы станет бесполезным — браузеру
    нечего показывать всё это время.
    """
    chart = await _chart_or_404(session, token)

    # Кто смотрит: владелец или человек, которому переслали ссылку. Разница
    # не в тексте (его видят оба — ссылка для того и нужна), а в кнопках:
    # генерация тратит лимиты и деньги ВЛАДЕЛЬЦА, поэтому запускать её со
    # своей стороны посторонний не может.
    is_owner = chart.user_id == user.id

    blocks = [
        await _report_block(session, chart, ReportType.FREE_NATAL, "Бесплатный разбор"),
        await _report_block(session, chart, ReportType.FULL_NATAL, "Полный разбор"),
    ]

    return templates.TemplateResponse(
        request,
        "report.html",
        {
            "user": user,
            "chart": chart,
            "natal": chart.calc["natal"],
            "is_owner": is_owner,
            "blocks": blocks,
            # Пока хоть один текст готовится, страница перечитывает себя.
            # Когда всё готово — обновление прекращается, иначе стенд сам
            # себе создавал бы бесконечный поток запросов.
            "refresh": REFRESH_SECONDS if any(b["running"] for b in blocks) else None,
            "thanks": "thanks" in request.query_params,
        },
    )


@router.post("/r/{token}/report")
async def request_report(
    token: str,
    user: UserDep,
    session: SessionDep,
    background: BackgroundTasks,
    report_type: Annotated[str, Form()] = ReportType.FULL_NATAL.value,
) -> Response:
    """Кнопка «получить разбор»: ставит генерацию в очередь и уводит обратно."""
    chart = await _chart_or_404(session, token)
    if chart.user_id != user.id:
        # 403, а не тихое игнорирование: посторонний с ссылкой не должен
        # тратить лимиты и деньги владельца карты.
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Это чужая карта")

    try:
        rtype = ReportType(report_type)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Неизвестный вид разбора")

    # Текст уже есть — просто возвращаем на страницу. Повторное нажатие не
    # должно ни платить второй раз, ни тревожить фоновую задачу.
    if await reports.get_report(session, chart.id, rtype) is not None:
        return RedirectResponse(f"/r/{token}", status_code=status.HTTP_303_SEE_OTHER)

    # Пишем «попросил» ДО генерации. Разница между этим счётчиком и
    # report_ready — доля неудач, которую иначе не увидеть.
    await events.log(
        session, events.REPORT_REQUESTED,
        user_id=user.id, chart_id=chart.id, type=rtype.value,
    )
    await session.commit()

    generation.schedule(background, chart, rtype)
    return RedirectResponse(f"/r/{token}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/r/{token}/feedback")
async def leave_feedback(
    token: str,
    user: UserDep,
    session: SessionDep,
    rating: Annotated[int, Form()] = 0,
    comment: Annotated[str, Form()] = "",
    report_type: Annotated[str, Form()] = ReportType.FREE_NATAL.value,
) -> Response:
    """Оценка разбора: 1–5 и необязательное «что не так».

    Пишется в `events`, а не в свою таблицу: таблица-поток для того и
    заведена, и отдельная миграция ради одного поля со звёздочками была бы
    расточительством.

    Оговорка про персональные данные: правило «в props их нет» здесь держится
    на самом тестере — он пишет про ТЕКСТ, но может процитировать в жалобе
    дату рождения. Поле короткое (500 символов) и заполняют его несколько
    знакомых людей; когда отзывы станут массовыми, их место — отдельная
    таблица, которую можно чистить по запросу человека.
    """
    chart = await _chart_or_404(session, token)

    if not 1 <= rating <= 5:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Оценка вне диапазона 1–5")

    await events.log(
        session, events.FEEDBACK,
        user_id=user.id,
        chart_id=chart.id,
        type=report_type,
        rating=rating,
        comment=comment.strip()[:MAX_COMMENT],
        # Свою карту оценивает владелец, чужую — тот, кому переслали ссылку.
        # В отчётах это разные по весу мнения.
        own=chart.user_id == user.id,
    )
    await session.commit()

    return RedirectResponse(f"/r/{token}?thanks=1", status_code=status.HTTP_303_SEE_OTHER)


# --- Внутренние помощники --------------------------------------------------


async def _chart_or_404(session: AsyncSession, token: str) -> Chart:
    """Карта по токену или честная 404.

    Токен и есть ключ доступа (см. charts.get_chart_by_token): 24 случайных
    байта не подобрать перебором, поэтому проверки владельца при чтении нет.
    """
    chart = await charts.get_chart_by_token(session, token)
    if chart is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Такой карты нет")
    return chart


async def _report_block(
    session: AsyncSession, chart: Chart, report_type: ReportType, title: str
) -> dict:
    """Всё, что шаблону нужно знать про один разбор, одним словарём.

    Четыре взаимоисключающих состояния, и разводятся они здесь, а не в
    шаблоне: логика «текста нет, но он готовится, если только не упал»
    внутри Jinja превращается в нечитаемую лесенку.
    """
    report = await reports.get_report(session, chart.id, report_type)
    state = generation.state(chart.id, report_type)

    return {
        "type": report_type.value,
        "title": title,
        # Текст готов — про состояние генерации уже неважно.
        "sections": reports.sections(report) if report is not None else None,
        "report": report,
        "running": report is None and state is not None and state.running,
        "error": None if report is not None or state is None else state.error,
    }


async def _index_with_error(
    request: Request, user: User, session: AsyncSession, message: str
) -> Response:
    """Показать форму заново с человеческим сообщением об ошибке.

    Отдельная функция, потому что ошибок ввода несколько, а страница одна.
    Статус 200, а не 400: это обычная страница для человека, а не ответ
    программе — и код 400 заставил бы некоторые браузеры показать свою
    страницу ошибки вместо нашей формы.
    """
    my_charts = await charts.list_charts(session, user.id)
    return templates.TemplateResponse(
        request,
        "index.html",
        {"user": user, "charts": my_charts, "error": message},
    )
