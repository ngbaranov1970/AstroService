# Команды uv

Шпаргалка по работе с `uv` в проекте. Все команды выполняются из папки `backend/` (там лежат `pyproject.toml` и `uv.lock`).

`uv` установлен глобально в `~/.local/bin` — активировать venv вручную не требуется, `uv run` сам его находит.

## Повседневная работа

```bash
uv sync                # поставить зависимости из uv.lock (только основные)
uv sync --extra dev    # + dev-группа (pytest, ruff)
```

```bash
uv run pytest          # запустить тесты
uv run python -m app.main
uv run ruff check .
```

`uv run <команда>` — самый частый способ запускать что угодно в venv проекта без `source .venv/bin/activate`.

## Управление зависимостями

```bash
uv add redis                 # добавить пакет (пишет в pyproject.toml и uv.lock)
uv add --dev pytest-cov      # добавить в dev-группу

uv remove redis              # удалить пакет
```

```bash
uv lock --upgrade                     # пересчитать lock, обновить все версии
uv lock --upgrade-package fastapi     # обновить только один пакет
uv sync                               # применить обновлённый lock (после uv lock)
```

Зависимости меняются только через `uv add` / `uv remove` / `uv lock` — не через `pip install` внутри venv (так возникает дублирование pip/uv в site-packages).

## venv и версии Python

```bash
uv venv --python 3.13     # создать чистый venv вручную (обычно не нужно — uv sync делает это сам)

uv python list            # доступные версии Python
uv python install 3.13    # поставить нужную версию
```

## Диагностика

```bash
uv pip list     # что реально стоит в venv (аналог pip list)
uv tree         # дерево зависимостей, поиск конфликтов версий
uv --version    # версия самого uv
```

## Важно

- `uv.lock` обязательно коммитить в git — гарантия одинаковых версий у всех разработчиков.
- Не устанавливать `pip`/`uv` как пакеты внутрь `.venv` — `uv` должен быть только глобальным инструментом (`~/.local/bin/uv`), venv — чистым от служебных пакетов.
