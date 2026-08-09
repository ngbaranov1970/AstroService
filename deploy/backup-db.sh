#!/usr/bin/env bash
# Ежедневный дамп базы стенда. Ставится в cron на сервере, см. docs/progress.md.
set -euo pipefail

# PATH в cron короче интерактивного (обычно /usr/bin:/bin) — docker найдётся,
# но задаём явно, чтобы поведение не зависело от настроек демона.
PATH=/usr/local/bin:/usr/bin:/bin

KEEP_DAYS=14
DEST="${BACKUP_DIR:-$HOME/backups}"

# Корень проекта — от места самого скрипта: у cron рабочий каталог свой,
# и относительный путь до docker-compose.yml не нашёлся бы.
PROJECT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$PROJECT_DIR"

# Имя базы и пользователь берутся из того же .env, что читает docker compose:
# отдельная копия значений однажды разъедется, и дамп пойдёт мимо базы.
DB_USER=$(grep -E '^POSTGRES_USER=' .env | cut -d= -f2-)
DB_NAME=$(grep -E '^POSTGRES_DB=' .env | cut -d= -f2-)
if [ -z "$DB_USER" ] || [ -z "$DB_NAME" ]; then
	echo "$(date '+%F %T') FAIL: в .env нет POSTGRES_USER или POSTGRES_DB" >&2
	exit 1
fi

# 700 и 600 ниже: в дампе имена, даты и места рождения живых людей, а
# домашний каталог по умолчанию читаем всем.
mkdir -p "$DEST"
chmod 700 "$DEST"

STAMP=$(date +%Y-%m-%d_%H%M)
OUT="$DEST/astro_$STAMP.dump"
TMP="$OUT.part"

# Прерванный дамп не должен осесть под рабочим именем: неполный файл выглядит
# как бэкап и обнаруживается только в момент восстановления.
trap 'rm -f "$TMP"' EXIT

# -Fc: сжатый формат pg_restore, из него достаётся отдельная таблица.
# exec, а не run: лёгший контейнер должен уронить скрипт, а не поднять второй.
docker compose exec -T db pg_dump -U "$DB_USER" -Fc "$DB_NAME" >"$TMP"

# Типовой тихий отказ — файл нулевой длины при живом cron (сменился пароль,
# база не поднялась). Пустая схема этой базы весит ~15 КБ, так что 10 КБ
# отделяет «пусто» от «мало данных».
SIZE=$(stat -c%s "$TMP")
if [ "$SIZE" -lt 10000 ]; then
	echo "$(date '+%F %T') FAIL: дамп $SIZE Б — слишком мало, не сохраняю" >&2
	exit 1
fi

mv "$TMP" "$OUT"
chmod 600 "$OUT"

find "$DEST" -maxdepth 1 -name 'astro_*.dump' -mtime +"$KEEP_DAYS" -delete

echo "$(date '+%F %T') ok: $OUT, $((SIZE / 1024)) КБ, копий в каталоге: $(find "$DEST" -maxdepth 1 -name 'astro_*.dump' | wc -l)"
