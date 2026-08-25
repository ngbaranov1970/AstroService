"""Рассылка гороскопа дня в личные сообщения VK.

Место в слоях: /admin (кнопка) → ЭТОТ ФАЙЛ → app/vk/api.py → api.vk.com.
Здесь знание «кому и что слать»: выборка получателей, знак каждого из них,
разбор отказов. Протокол VK живёт этажом ниже, текст письма — в posts.

Письмо — это вступление про день плюс абзац знака человека, оба взяты из
готового поста daily_horoscope. Своего текста рассылка не пишет и в LLM не
ходит: двенадцать абзацев написаны заранее на всю базу сразу, и стоимость
рассылки не зависит от числа подписчиков.

Внутри VK платного нет вовсе (docs/astro-context.md, «Рассылка ведёт в Mini
App»), поэтому письмо ничего не продаёт: кнопка ведёт в приложение, к уже
посчитанному разбору.
"""

from __future__ import annotations

import logging
import zlib
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.prompts import daily_horoscope
from app.models import Chart, Event, Platform, PostKind, User
from app.services import content, events
from app.vk import api as vk_api

logger = logging.getLogger(__name__)

# Надпись на кнопке под письмом. Обещает ровно то, что человек увидит,
# нажав: свою карту, а не «подробности».
BUTTON_LABEL = "Открыть свою карту"


@dataclass(slots=True)
class Recipient:
    """Один получатель: кому слать и какой абзац ему достанется."""

    user_id: int
    vk_id: int
    sign: str


@dataclass(slots=True)
class BroadcastReport:
    """Итог рассылки — то, что видит человек, нажавший кнопку в админке."""

    target_date: date
    delivered: int = 0
    failed: int = 0
    skipped_already_sent: int = 0
    # Коды отказов VK и сколько раз каждый встретился: 901 — отозвал
    # разрешение, 900 — чёрный список, 902 — настройки приватности.
    errors: dict[int, int] = field(default_factory=dict)
    # Заполнено — рассылка оборвалась на полпути (протух ключ, спам-лимит).
    # Остальные поля при этом верны: они про то, что успело уйти.
    aborted: str | None = None

    @property
    def attempted(self) -> int:
        return self.delivered + self.failed


@dataclass(slots=True)
class Preview:
    """Что показать в админке до отправки: охват, письма и причина отказа."""

    target_date: date
    total: int = 0
    already_sent: int = 0
    # Знак → сколько человек его получит. Нули не хранятся: знаков без
    # получателей в разбивке нет вовсе.
    by_sign: dict[str, int] = field(default_factory=dict)
    # Знак → готовое письмо целиком. Двенадцать текстов вместо одного
    # образца: перед отправкой их и вычитывают.
    letters: dict[str, str] = field(default_factory=dict)
    # Человеческая причина, по которой кнопку жать нельзя. None — можно.
    problem: str | None = None


def sun_sign(calc: dict) -> str | None:
    """Знак Солнца из расчёта карты по-русски («Лев») или None, если его нет.

    Строки здесь те же, что в daily_horoscope.SIGN_ORDER: обе стороны берут
    их из astro_core.natal.SIGNS_RU, поэтому словарь-переходник не нужен.
    """
    for planet in calc.get("planets", []):
        if planet.get("name") == "Sun":
            return planet.get("sign_ru")
    return None


async def recipients(session: AsyncSession, target_date: date) -> list[Recipient]:
    """Кому уходит рассылка за этот день, уже без тех, кому её отправили.

    Четыре условия отбора разной природы: канал (VK), согласие (subscribed),
    разрешение писать от самого VK (vk_messages_allowed_at) и наличие своей
    карты — без неё неизвестен знак, а значит нечего слать.
    """
    # TODO: доставать знак выражением jsonb_path_query_first, когда получателей
    # станут тысячи — сейчас на каждого едет весь calc, это ~11 КБ.
    stmt = (
        select(User.id, User.external_id, Chart.calc)
        .join(Chart, Chart.id == User.primary_chart_id)
        .where(
            User.platform == Platform.VK,
            User.subscribed.is_(True),
            User.deleted_at.is_(None),
            User.bot_blocked_at.is_(None),
            User.vk_messages_allowed_at.is_not(None),
        )
    )
    rows = (await session.execute(stmt)).all()
    already = await _already_sent(session, target_date)

    out: list[Recipient] = []
    for user_id, external_id, calc in rows:
        if user_id in already:
            continue
        sign = sun_sign(calc or {})
        if sign is None:
            # Карта есть, а Солнца в ней нет — расчёт битый. Это не про
            # рассылку, поэтому просто пропускаем и оставляем след в логе.
            logger.warning("В карте пользователя %s нет Солнца, пропущен", user_id)
            continue
        out.append(Recipient(user_id=user_id, vk_id=int(external_id), sign=sign))
    return out


async def _already_sent(session: AsyncSession, target_date: date) -> set[int]:
    """id тех, кому письмо за этот день уже доставлено.

    Защита от второго запуска, более долгая, чем встроенная в VK: тот держит
    random_id всего час, а рассылку могут повторить и вечером.
    """
    stmt = select(Event.user_id).where(
        Event.name == events.BROADCAST_SENT,
        Event.props["date"].astext == target_date.isoformat(),
    )
    return {uid for uid in await session.scalars(stmt) if uid is not None}


def compose(intro: str, sign: str, sign_text: str) -> str:
    """Текст письма: общее вступление про день и абзац знака.

    Знак отдельной строкой — в переписке VK разметки нет, и это единственный
    способ отделить «сегодня вообще» от «сегодня у тебя».
    """
    return f"{intro.strip()}\n\n{sign}\n{sign_text.strip()}"


def _random_id(target_date: date, sign: str) -> int:
    """Стабильное число для messages.send: одна дата и знак — одно значение.

    Случайное здесь означало бы вторую копию письма при перезапуске; VK
    отбрасывает повтор по паре (адресат, random_id). Знак входит в ключ,
    потому что каждой из двенадцати групп уходит свой текст.
    """
    # crc32 берёт 32 бита, старший бит гасим: random_id у VK знаковый.
    return zlib.crc32(f"{target_date.isoformat()}:{sign}".encode()) & 0x7FFF_FFFF


async def letters_for(session: AsyncSession, target_date: date) -> dict[str, str]:
    """Двенадцать готовых писем за этот день: знак → полный текст.

    Бросает LookupError, если поста нет или он не разобрался: рассылать
    нечего, и пустой словарь скрыл бы причину.
    """
    post = await content.find_post(session, PostKind.DAILY_HOROSCOPE, target_date)
    if post is None:
        raise LookupError(f"Гороскопа за {target_date} нет — сначала сгенерируйте пост")

    parsed = daily_horoscope.parse(post.text)
    if parsed is None:
        raise LookupError(f"Гороскоп за {target_date} не разобрался, рассылать нечего")

    intro, signs = parsed
    return {s["sign"]: compose(intro, s["sign"], s["text"]) for s in signs}


async def preview(session: AsyncSession, target_date: date | None = None) -> Preview:
    """Собрать всё, что админка показывает до нажатия кнопки.

    Ошибку не бросает: отсутствие поста — это не сбой, а обычное состояние
    экрана, и человеку надо показать причину, а не страницу 500.
    """
    target_date = target_date or content.today_msk()
    out = Preview(target_date=target_date)

    try:
        out.letters = await letters_for(session, target_date)
    except LookupError as e:
        out.problem = str(e)
        return out

    people = await recipients(session, target_date)
    out.total = len(people)
    out.already_sent = len(await _already_sent(session, target_date))
    for person in people:
        out.by_sign[person.sign] = out.by_sign.get(person.sign, 0) + 1

    if not people:
        out.problem = (
            f"Получателей нет: за {target_date} письмо уже ушло всем"
            if out.already_sent
            else "Получателей нет: никто не разрешил сообществу писать"
        )
    return out


async def run(
    session: AsyncSession, target_date: date | None = None, *, dry_run: bool = False
) -> BroadcastReport:
    """Разослать гороскоп за день. Без даты — за сегодня по Москве.

    dry_run считает получателей и ничего не отправляет — им проверяют охват
    перед настоящим запуском.

    Бросает LookupError, если поста за этот день нет или он не разобрался.
    Отказ доставки отдельному человеку ошибкой не считается — он в отчёте.
    """
    target_date = target_date or content.today_msk()
    letters = await letters_for(session, target_date)

    report = BroadcastReport(target_date=target_date)
    people = await recipients(session, target_date)
    if dry_run:
        report.delivered = len(people)
        return report

    keyboard = vk_api.open_app_keyboard(BUTTON_LABEL, launch_hash=_launch_hash(target_date))

    # Отправляем группами по знаку: текст у группы один, а транспорт шлёт
    # один текст на вызов. Двенадцать вызовов вместо письма на человека.
    by_sign: dict[str, list[Recipient]] = {}
    for person in people:
        by_sign.setdefault(person.sign, []).append(person)

    for sign, group in by_sign.items():
        letter = letters.get(sign)
        if letter is None:
            # parse() уже проверил, что знаки все двенадцать, — сюда можно
            # попасть только при опечатке в SIGNS_RU. Группу пропускаем.
            logger.error("В гороскопе за %s нет знака %s", target_date, sign)
            continue

        by_vk_id = {person.vk_id: person for person in group}
        try:
            results = await vk_api.send_messages(
                list(by_vk_id),
                letter,
                random_id=_random_id(target_date, sign),
                keyboard=keyboard,
            )
        except vk_api.VkError as e:
            # Сорвался весь вызов — ключ, сеть или спам-ограничение на нас.
            # Дальше по знакам не идём (причина общая для всех), но и наверх
            # не бросаем: иначе откатятся отметки об уже ушедших письмах, и
            # следующий запуск отправит их повторно.
            logger.error("Рассылка за %s оборвалась на знаке %s: %s", target_date, sign, e)
            report.aborted = str(e)
            break
        await _record(session, results, by_vk_id, report, target_date, sign)

    logger.info(
        "Рассылка за %s: доставлено %s, отказов %s, пропущено %s",
        target_date,
        report.delivered,
        report.failed,
        report.skipped_already_sent,
    )
    return report


def _launch_hash(target_date: date) -> str:
    """Метка канала в строке запуска приложения: из какого письма пришли."""
    return f"b={target_date.isoformat()}"


async def _record(
    session: AsyncSession,
    results: list[vk_api.SendResult],
    by_vk_id: dict[int, Recipient],
    report: BroadcastReport,
    target_date: date,
    sign: str,
) -> None:
    """Разложить ответ VK в события и отчёт, погасив разрешение отказавшим."""
    denied: list[int] = []
    for result in results:
        person = by_vk_id.get(result.user_id)
        if person is None:
            # VK ответил про адресата, которого мы не спрашивали. Такого быть
            # не должно, но падать из-за этого посреди рассылки незачем.
            logger.error("VK ответил про постороннего адресата %s", result.user_id)
            continue

        if result.delivered:
            report.delivered += 1
            await events.log(
                session,
                events.BROADCAST_SENT,
                user_id=person.user_id,
                date=target_date.isoformat(),
                sign=sign,
            )
            continue

        report.failed += 1
        code = result.error_code or 0
        report.errors[code] = report.errors.get(code, 0) + 1
        await events.log(
            session,
            events.BROADCAST_FAILED,
            user_id=person.user_id,
            date=target_date.isoformat(),
            code=code,
        )
        if code == vk_api.ERROR_NO_PERMISSION:
            denied.append(person.user_id)

    if denied:
        # Пакетным UPDATE, а не users.mark_vk_messages_denied по одному: та
        # работает с загруженным объектом, а здесь у нас только идентификаторы.
        # Поле и его смысл те же — «разрешения больше нет».
        await session.execute(
            update(User).where(User.id.in_(denied)).values(vk_messages_allowed_at=None)
        )
        for user_id in denied:
            await events.log(session, events.GROUP_MESSAGES_DENIED, user_id=user_id, code=901)
