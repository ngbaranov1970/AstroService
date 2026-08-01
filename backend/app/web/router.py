"""Маршруты веб-стенда: HTML прямо из FastAPI, без сборки и npm.

Слой тонкий по замыслу: принять запрос, позвать `app/services/*`, отдать
шаблон. Ни расчётов, ни SQL здесь нет — те же сервисы позже дёрнут бот и
Mini App, и поведение должно совпасть. Соседи по папке: `deps.py` — кто
пришёл, `forms.py` — проверка ввода, `generation.py` — фоновая генерация.

Форма маршрутов продиктована тем, что LLM отвечает 30–60 с: генерацию
ставит фоновая задача, POST отвечает 303, GET только читает базу, а
страница разбора перечитывает себя сама. Разбор решений — в
docs/backend-structure.md, раздел про `app/web/`.
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

# Один объект на процесс: внутри него кеш скомпилированных шаблонов.
# Автоэкранирование .html включено по умолчанию — имя и город из формы
# попадают в страницу безопасно.
templates = Jinja2Templates(directory=TEMPLATES_DIR)

router = APIRouter(
    # /docs описывает JSON-API для Mini App, а здесь HTML-страницы для людей.
    include_in_schema=False,
)

# Период автообновления страницы, пока текст готовится: реже раздражает
# ожиданием, чаще плодит лишние SELECT и строки в логе доступа.
REFRESH_SECONDS = 5

# Потолок длины комментария в отзыве: поле свободное, и без ограничения
# в JSONB аналитики уедет чья-нибудь вставленная целиком статья.
MAX_COMMENT = 500


# --- Страницы --------------------------------------------------------------

# Тип-сокращение: и сессия, и пользователь нужны почти каждому обработчику.
SessionDep = Annotated[AsyncSession, Depends(get_session)]
UserDep = Annotated[User, Depends(current_user)]


@router.get("/")
async def index(request: Request, user: UserDep, session: SessionDep) -> Response:
    """Форма ввода данных рождения и список уже посчитанных карт."""
    # Секрет из /?t=<секрет> уводим сразу, как только current_user проставил
    # тестера: иначе он осядет в истории браузера и уедет в заголовке Referer
    # на первый же внешний сайт.
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

    Отдельный шаг существует потому, что «Ростов» — два разных города в
    тысяче километров друг от друга, и выбрать между ними может только
    человек. Данные первого шага едут скрытыми полями формы. Поиск в базу
    не ходит: справочник городов лежит в памяти процесса.
    """
    try:
        birth_local = forms.parse_birth(birth_date, birth_time, time_known)
    except forms.FormError as exc:
        return await _index_with_error(request, user, session, str(exc))

    found = charts.search_places(city)
    if not found:
        return await _index_with_error(
            request,
            user,
            session,
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
            # Разобранное время показываем на шаге выбора города, чтобы
            # опечатка в дате всплыла до расчёта.
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

    # Таймзону берём по координатам, а не из скрытого поля: подделанное имя
    # зоны уронило бы расчёт, а правдоподобное чужое — молча сдвинуло бы всю
    # карту на часы.
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
        session,
        events.CHART_CREATED,
        user_id=user.id,
        chart_id=chart.id,
        time_known=time_known,
    )
    await session.commit()

    # Бесплатный разбор запускаем сразу — он и есть приманка. Полный только
    # по кнопке, иначе каждая карта стоит вдвое, включая недолистанные.
    if await reports.get_report(session, chart.id, ReportType.FREE_NATAL) is None:
        generation.schedule(background, chart, ReportType.FREE_NATAL)

    # 303, а не 302: браузер обязан перейти GET'ом, тогда «обновить» перечитает
    # разбор, а не переотправит форму.
    return RedirectResponse(f"/r/{chart.token}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/r/{token}")
async def report_page(request: Request, token: str, user: UserDep, session: SessionDep) -> Response:
    """Страница карты: расчёт готов сразу, тексты догружаются.

    Обработчик только читает. Генерация здесь держала бы ответ минуту, и
    автообновление страницы стало бы бесполезным.
    """
    chart = await _chart_or_404(session, token)

    # Текст видят оба — ссылку для того и пересылают. Кнопки только у
    # владельца: генерация тратит его лимиты и деньги.
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
            # Когда всё готово, автообновление прекращается — иначе стенд сам
            # себе создаёт бесконечный поток запросов.
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

    # Повторное нажатие не должно ни платить второй раз, ни тревожить
    # фоновую задачу.
    if await reports.get_report(session, chart.id, rtype) is not None:
        return RedirectResponse(f"/r/{token}", status_code=status.HTTP_303_SEE_OTHER)

    # Пишем «попросил» ДО генерации: разница Не вижу тут ничего про админ секциюс report_ready и есть доля
    # неудач, которую иначе не увидеть.
    await events.log(
        session,
        events.REPORT_REQUESTED,
        user_id=user.id,
        chart_id=chart.id,
        type=rtype.value,
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

    Пишется в `events`, а не в свою таблицу: миграция ради одного поля со
    звёздочками не окупается. Правило «в props нет ПДн» здесь держится на
    самом тестере — он может процитировать в жалобе дату рождения. Когда
    отзывов станет много, их место — отдельная таблица, которую чистят
    по запросу человека.
    """
    chart = await _chart_or_404(session, token)

    if not 1 <= rating <= 5:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Оценка вне диапазона 1–5")

    await events.log(
        session,
        events.FEEDBACK,
        user_id=user.id,
        chart_id=chart.id,
        type=report_type,
        rating=rating,
        comment=comment.strip()[:MAX_COMMENT],
        # Мнение владельца и мнение того, кому переслали ссылку, в отчётах
        # весят по-разному.
        own=chart.user_id == user.id,
    )
    await session.commit()

    return RedirectResponse(f"/r/{token}?thanks=1", status_code=status.HTTP_303_SEE_OTHER)


# --- Внутренние помощники --------------------------------------------------


async def _chart_or_404(session: AsyncSession, token: str) -> Chart:
    """Карта по токену или 404.

    Проверки владельца при чтении нет: токен и есть ключ доступа — 24
    случайных байта не подобрать перебором (см. charts.get_chart_by_token).
    """
    chart = await charts.get_chart_by_token(session, token)
    if chart is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Такой карты нет")
    return chart


async def _report_block(
    session: AsyncSession, chart: Chart, report_type: ReportType, title: str
) -> dict:
    """Всё, что шаблону нужно знать про один разбор, одним словарём.

    Четыре взаимоисключающих состояния разводятся здесь, а не в шаблоне:
    «текста нет, но он готовится, если только не упал» внутри Jinja
    превращается в нечитаемую лесенку.
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

    Статус 200, а не 400: это страница для человека, а 400 заставил бы часть
    браузеров показать свою страницу ошибки вместо нашей формы.
    """
    my_charts = await charts.list_charts(session, user.id)
    return templates.TemplateResponse(
        request,
        "index.html",
        {"user": user, "charts": my_charts, "error": message},
    )
