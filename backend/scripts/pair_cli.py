"""Пары в терминале: собрать, позвать ссылкой, принять партнёра — на живой базе.

Место в слоях: ЭТОТ ФАЙЛ → app/services/pairs.py (+ charts.py, users.py) →
Postgres. Своей логики здесь нет: разбор аргументов, сессия, вызов сервиса,
коммит.

Отдельно от synastry_cli.py потому, что тот считает чистой функцией и базы не
видит вовсе. Здесь проверяется ровно то, чего он не трогает: дедуп пары, срок
жизни приглашения, замок «партнёр пришёл в двух вкладках», принадлежность карт.

Текста разбора скрипт не печатает: промптов пары ещё нет (фаза 3 плана в
docs/compatibility-plan.md).

Примеры:
    uv run python scripts/pair_cli.py make --user web:pair-cli --kind romance \\
        1990-05-11 17:20 Касимов 1992-03-02 09:15 Рязань
    uv run python scripts/pair_cli.py make --user 7 --kind family --invite \\
        --label Коля 1990-05-11 17:20 Касимов
    uv run python scripts/pair_cli.py join <invite_token> 1992-03-02 09:15 Рязань
    uv run python scripts/pair_cli.py show --user 7 <pair_token>
    uv run python scripts/pair_cli.py list --user 7

Токен приглашения печатается в stdout — для ручного прогона иначе никак, но в
логи он не идёт ни здесь, ни в сервисах: по смыслу это пароль.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

# Скрипт запускают из каталога backend; добавляем его в путь импорта,
# чтобы app и astro_core находились без установки пакета.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import dispose_engine, get_sessionmaker
from app.logging_setup import setup_logging
from app.models import Chart, Pair, PairKind, Platform, User
from app.services import charts as charts_service
from app.services import pairs as pairs_service
from app.services import users as users_service

# Время-заглушка для карты, у которой оно неизвестно. Само значение всё равно
# переставит create_chart (UNKNOWN_TIME), здесь важно лишь разобрать аргумент.
UNKNOWN_MARK = "-"


# --- Разбор аргументов в объекты базы ---------------------------------------


async def _resolve_user(session: AsyncSession, spec: str) -> User:
    """Пользователь по «7» или по «web:внешний-id».

    Второй вид заводит недостающего: для ручного прогона нужен хоть какой-то
    инициатор, а регистрации в сервисе нет.
    """
    if spec.isdigit():
        user = await users_service.get_by_id(session, int(spec))
        if user is None:
            sys.exit(f"Пользователя {spec} нет")
        return user

    platform_name, _, external_id = spec.partition(":")
    if not external_id:
        sys.exit("--user: нужен id числом или «платформа:внешний-id», например web:pair-cli")
    try:
        platform = Platform(platform_name)
    except ValueError:
        sys.exit(f"Платформа {platform_name!r}: бывают {', '.join(p.value for p in Platform)}")

    return await users_service.get_or_create(
        session, platform=platform, external_id=external_id, source="pair_cli"
    )


def _resolve_place(query: str, pick: int) -> charts_service.Place:
    """Место по названию города, с выбором из подсказок.

    Подсказки — в stderr: stdout остаётся под данные пары, которые удобно
    перенаправить в файл.
    """
    found = charts_service.search_places(query)
    if not found:
        sys.exit(f"Город не найден: {query!r}")

    if len(found) > 1:
        print(f"«{query}»: выбран отмеченный, другой — через --pick/--pick-b:", file=sys.stderr)
        for i, place in enumerate(found, 1):
            mark = "→" if i == pick else " "
            print(f" {mark} {i}. {place.label}, {place.tz}", file=sys.stderr)
        print(file=sys.stderr)

    if not 1 <= pick <= len(found):
        sys.exit(f"--pick {pick}: вариантов всего {len(found)}")
    return found[pick - 1]


def _birth_moment(date_str: str, time_str: str) -> tuple[datetime, bool]:
    """Момент рождения и флаг «время известно» из строк аргументов.

    Полдень при неизвестном времени не подставляем — это делает create_chart
    до вычисления хеша; подставь мы своё значение, та же карта записалась бы
    второй строкой.
    """
    try:
        day = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        sys.exit(f"Дата {date_str!r}: ожидается ГГГГ-ММ-ДД")

    if time_str == UNKNOWN_MARK:
        return datetime.combine(day, datetime.min.time()), False

    try:
        moment = datetime.strptime(time_str, "%H:%M").time()
    except ValueError:
        sys.exit(f"Время {time_str!r}: ожидается ЧЧ:ММ или «{UNKNOWN_MARK}»")
    return datetime.combine(day, moment), True


async def _make_chart(
    session: AsyncSession, *, user_id: int, date_str: str, time_str: str, city: str, pick: int
) -> Chart:
    """Карта одной стороны: город → место → строка в charts."""
    birth_local, time_known = _birth_moment(date_str, time_str)
    place = _resolve_place(city, pick)
    return await charts_service.create_chart(
        session,
        user_id=user_id,
        birth_local=birth_local,
        place=place,
        time_known=time_known,
    )


# --- Печать -----------------------------------------------------------------


async def _print_pair(session: AsyncSession, pair: Pair) -> None:
    """Состав пары и сводка расчёта — то, по чему видно, что сервис отработал."""
    print(f"\nПара {pair.id}   токен {pair.token}   тип {pair.kind.value}")
    if pair.inviter_label:
        print(f"  Подпись инициатора: {pair.inviter_label}")

    # session.get, а не pair.chart_a: связи объявлены lazy="raise_on_sql",
    # обращение к ним из async-кода падает.
    for side, chart_id in (("A", pair.chart_a_id), ("B", pair.chart_b_id)):
        if chart_id is None:
            print("  B: ждёт партнёра")
            continue
        chart = await session.get(Chart, chart_id)
        if chart is None:
            print(f"  {side}: карта {chart_id} исчезла")
            continue
        when = (
            chart.birth_local.strftime("%Y-%m-%d %H:%M") if chart.time_known else "время неизвестно"
        )
        print(f"  {side}: карта {chart.id}, {when}, {chart.place_name}")

    if pair.invite_token is not None:
        state = "живо" if pairs_service.invite_is_open(pair) else "закрыто"
        print(f"  Приглашение: {state}, до {pair.invite_expires_at}")

    if pair.calc is None:
        print("  Расчёта нет: пара не собрана")
        return

    summary = pair.calc["summary"]
    print(
        f"  Расчёт {pair.calc_version}: аспектов {len(pair.calc['aspects'])}, "
        f"в промпт идут {len(summary['top'])}, гармония {summary['harmony']}"
    )
    # Слева всегда сторона A: аспекты несимметричны, и «его Сатурн на её Луне»
    # с обратным — разные сюжеты. Подпись нужна, чтобы это не читалось наугад.
    print("  Верхушка по весу (слева A, справа B):")
    for aspect in summary["top"][:5]:
        print(
            f"    {aspect['weight']:.3f}  {aspect['a_ru']:<14} {aspect['type_ru']:<10} "
            f"{aspect['b_ru']:<14} орб {aspect['orb']}°"
        )


# --- Подкоманды -------------------------------------------------------------


async def _cmd_make(session: AsyncSession, args: argparse.Namespace) -> None:
    """Собрать пару: карта инициатора, карта партнёра (если задана), тип связи."""
    user = await _resolve_user(session, args.user)

    chart_a = await _make_chart(
        session,
        user_id=user.id,
        date_str=args.date,
        time_str=args.time,
        city=args.city,
        pick=args.pick,
    )

    chart_b = None
    if args.date_b is not None:
        if args.time_b is None or args.city_b is None:
            sys.exit("Для второй стороны нужны три значения: дата, время, город")
        chart_b = await _make_chart(
            session,
            user_id=user.id,
            date_str=args.date_b,
            time_str=args.time_b,
            city=args.city_b,
            pick=args.pick_b,
        )

    try:
        pair = await pairs_service.create_pair(
            session,
            user_id=user.id,
            chart_a=chart_a,
            chart_b=chart_b,
            kind=PairKind(args.kind),
            inviter_label=args.label,
        )
    except ValueError as e:
        sys.exit(f"Пара не собрана: {e}")

    if args.invite:
        if chart_b is not None:
            sys.exit("--invite на собранной паре бессмысленно: звать некого")
        token, expires = await pairs_service.create_invite(session, pair)
        print(f"Приглашение: {token} (до {expires:%d.%m.%Y %H:%M} UTC)")

    await _print_pair(session, pair)


async def _cmd_join(session: AsyncSession, args: argparse.Namespace) -> None:
    """Приход партнёра по ссылке: его данные → карта у инициатора → сторона B.

    Карта заводится в аккаунте инициатора, а не партнёра: своего аккаунта у
    того может не быть вовсе — он пришёл по ссылке и никем не опознан.
    """
    pair = await pairs_service.get_pair_by_invite(session, args.invite_token)
    if pair is None:
        # Ровно тот же ответ, что отдаст роутер инвайтов: нет токена, протух
        # и уже использован неразличимы для того, кто перебирает.
        sys.exit("Приглашение не найдено")

    chart_b = await _make_chart(
        session,
        user_id=pair.user_id,
        date_str=args.date,
        time_str=args.time,
        city=args.city,
        pick=args.pick,
    )

    try:
        pair = await pairs_service.attach_partner(session, pair, chart_b)
    except ValueError as e:
        sys.exit(f"Партнёр не прицеплен: {e}")

    await _print_pair(session, pair)


async def _cmd_show(session: AsyncSession, args: argparse.Namespace) -> None:
    """Пара по публичному токену. Владелец проверяется — как и в API."""
    user = await _resolve_user(session, args.user)
    pair = await pairs_service.get_pair_by_token(session, args.token, user_id=user.id)
    if pair is None:
        sys.exit("Пары с таким токеном у этого пользователя нет")
    await _print_pair(session, pair)


async def _cmd_list(session: AsyncSession, args: argparse.Namespace) -> None:
    """Все пары пользователя, включая ждущие партнёра."""
    user = await _resolve_user(session, args.user)
    found = await pairs_service.list_pairs(session, user.id)
    if not found:
        print(f"У пользователя {user.id} пар нет")
        return
    print(f"Пар у пользователя {user.id}: {len(found)}")
    for pair in found:
        state = "собрана" if pair.chart_b_id is not None else "ждёт партнёра"
        print(f"  {pair.id}  {pair.kind.value:<8} {state:<14} {pair.token}")


COMMANDS = {"make": _cmd_make, "join": _cmd_join, "show": _cmd_show, "list": _cmd_list}


# --- Точка входа ------------------------------------------------------------


def _add_birth_args(parser: argparse.ArgumentParser) -> None:
    """Тройка «дата, время, город» — она повторяется у make и join."""
    parser.add_argument("date", help="дата рождения, ГГГГ-ММ-ДД")
    parser.add_argument("time", help=f"местное время, ЧЧ:ММ или «{UNKNOWN_MARK}», если неизвестно")
    parser.add_argument("city", help="город рождения (поиск по базе GeoNames)")
    parser.add_argument("--pick", type=int, default=1, help="номер города из подсказок")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Пары и приглашения на живой базе")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="откатить транзакцию: расчёт видно, строк в базе не остаётся",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    make = subparsers.add_parser("make", help="собрать пару")
    make.add_argument("--user", required=True, help="id пользователя или «платформа:внешний-id»")
    make.add_argument(
        "--kind",
        required=True,
        choices=[k.value for k in PairKind],
        help="тип связи: он идёт в промпт и меняет текст",
    )
    _add_birth_args(make)
    # Вторая сторона необязательна: без неё выходит пара, ждущая партнёра.
    make.add_argument("date_b", nargs="?", help="дата рождения партнёра")
    make.add_argument("time_b", nargs="?", help="местное время партнёра")
    make.add_argument("city_b", nargs="?", help="город рождения партнёра")
    make.add_argument("--pick-b", type=int, default=1, help="номер города партнёра из подсказок")
    make.add_argument("--label", help="как назвать инициатора партнёру (inviter_label)")
    make.add_argument("--invite", action="store_true", help="выдать ссылку-приглашение")

    join = subparsers.add_parser("join", help="прийти по приглашению")
    join.add_argument("invite_token", help="токен из ссылки")
    _add_birth_args(join)

    show = subparsers.add_parser("show", help="показать пару по токену")
    show.add_argument("--user", required=True, help="владелец пары")
    show.add_argument("token", help="публичный токен пары")

    listing = subparsers.add_parser("list", help="пары пользователя")
    listing.add_argument("--user", required=True, help="id пользователя или «платформа:внешний-id»")

    return parser.parse_args()


async def _run(args: argparse.Namespace) -> None:
    sessionmaker = get_sessionmaker()
    try:
        async with sessionmaker() as session:
            await COMMANDS[args.command](session, args)
            # Коммит здесь, а не в сервисах: транзакцией управляет вызывающий.
            if args.dry_run:
                await session.rollback()
                # stdout сбрасываем руками: он буферизован, stderr нет, и без
                # flush сообщение об откате печатается раньше самой пары.
                sys.stdout.flush()
                print("\n--dry-run: изменения откачены", file=sys.stderr)
            else:
                await session.commit()
    finally:
        # Незакрытый пул asyncpg держит процесс — скрипт не завершится сам.
        await dispose_engine()


def main() -> None:
    args = _parse_args()
    # Логирование настраивает только точка входа (правило CLAUDE.md); WARNING,
    # чтобы INFO сервисов не мешался с выводом пары.
    setup_logging("WARNING")
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
