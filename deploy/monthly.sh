#!/usr/bin/env bash
# Гороскоп на месяц в posts. Cron 1-го числа, см. docs/progress.md.
set -euo pipefail

PATH=/usr/local/bin:/usr/bin:/bin

PROJECT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$PROJECT_DIR"

echo "$(date '+%F %T') месяц: запуск${*:+ (}${*}${*:+)}"

if docker compose exec -T api python scripts/monthly_cli.py "$@"; then
	echo "$(date '+%F %T') ok"
else
	echo "$(date '+%F %T') FAIL: месячный гороскоп не записался" >&2
	exit 1
fi
