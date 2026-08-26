"""Рассылка гороскопа дня из терминала — тем же вызовом, что кнопка админки.

Место в слоях: cron (deploy/broadcast.sh) → ЭТОТ ФАЙЛ →
app/services/broadcast.py → app/vk/api.py. Своей логики рассылки здесь нет:
скрипт открывает сессию, зовёт run(), коммитит и переводит отчёт в код
выхода, по которому cron отличает ушедшую рассылку от пропущенного дня.

Примеры:
    uv run python scripts/broadcast_cli.py                  # за сегодня по Москве
    uv run python scripts/broadcast_cli.py --date 2026-08-25
    uv run python scripts/broadcast_cli.py --dry-run        # охват без отправки

Коды выхода: 0 — рассылка прошла, 1 — слать было нечего или вызов оборвался.
Отказ отдельному человеку (901, 900, 902) успехом быть не перестаёт: он
попадает в строку итога, а не в код выхода.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

# Скрипт запускают из каталога backend; добавляем его в путь импорта,
# чтобы app находился без установки пакета.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import dispose_engine, get_sessionmaker
from app.logging_setup import setup_logging
from app.services import broadcast
from app.vk import api as vk_api


async def _run(target_date: date | None, *, dry_run: bool) -> int:
    """Отправить рассылку и вернуть код выхода процесса."""
    sessionmaker = get_sessionmaker()
    try:
        async with sessionmaker() as session:
            try:
                report = await broadcast.run(session, target_date, dry_run=dry_run)
            except LookupError as e:
                # Поста за этот день нет или он не разобрался. Для админки это
                # обычное состояние экрана, а для cron — повод показаться в
                # логе: тексты генерируются руками, и тишина значит «забыли».
                print(f"рассылка не запущена: {e}", file=sys.stderr)
                return 1
            # Коммит здесь, а не в сервисе: письма уже ушли, и отметки об этом
            # обязаны пережить процесс — иначе завтрашний запуск отправит их
            # повторно (защита VK по random_id живёт всего час).
            await session.commit()
    finally:
        # Закрываем в обратном порядке открытию и в любом исходе: незакрытый
        # httpx-клиент держит процесс, а cron ждёт его завершения.
        await vk_api.close_client()
        await dispose_engine()

    _print_report(report, dry_run=dry_run)
    return 1 if report.aborted else 0


def _print_report(report: broadcast.BroadcastReport, *, dry_run: bool) -> None:
    """Одна строка итога в stdout: её и читают в логе cron.

    Дублирует INFO из сервиса намеренно: там нет кодов отказов, а именно они
    объясняют, почему охват на следующий день оказался меньше.
    """
    if dry_run:
        print(f"{report.target_date}: получателей {report.delivered}, ничего не отправлено")
        return

    line = f"{report.target_date}: доставлено {report.delivered}, отказов {report.failed}"
    if report.errors:
        codes = ", ".join(f"{code} — {count}" for code, count in sorted(report.errors.items()))
        line += f" (коды: {codes})"
    print(line)

    if report.aborted:
        print(f"рассылка оборвалась: {report.aborted}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Рассылка гороскопа дня в личные сообщения VK")
    parser.add_argument(
        "--date",
        help="день гороскопа, ГГГГ-ММ-ДД (по умолчанию сегодня по Москве)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="посчитать получателей и выйти, ничего не отправляя",
    )
    args = parser.parse_args()

    target_date: date | None = None
    if args.date:
        try:
            target_date = date.fromisoformat(args.date)
        except ValueError:
            # Здесь, в отличие от _parse_day в админке, мусор роняет запуск:
            # там опечатка в адресе не должна ломать экран, а тут молчаливая
            # подстановка «сегодня» отправила бы письма не за тот день.
            sys.exit(f"--date {args.date!r}: ожидается ГГГГ-ММ-ДД")

    # Логирование настраивает точка входа, а этот скрипт ею и является
    # (CLAUDE.md): без вызова сообщения сервиса о ходе рассылки в лог cron
    # не попадут вовсе.
    setup_logging()
    sys.exit(asyncio.run(_run(target_date, dry_run=args.dry_run)))


if __name__ == "__main__":
    main()
