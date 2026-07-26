"""Настройки backend: чтение backend/.env через pydantic-settings.

Два принципа, по которым сделан этот файл:

1. Описываем ТОЛЬКО те переменные, которые уже используются. Сейчас это
   ключ DeepSeek и строка подключения к Postgres. VK, JWT и ЮKassa появятся
   здесь тогда же, когда появится читающий их код, — иначе обязательное поле
   без значения роняет валидацию на пустом месте.

2. Настройки создаются лениво, через get_settings(), а не как переменная
   уровня модуля. Разница принципиальная: `settings = Settings()` в теле
   модуля означает, что ЛЮБОЙ импорт app.config без заполненного .env
   падает с ValidationError — включая pytest, alembic и скрипты из
   scripts/, которым ключ DeepSeek вообще не нужен. При ленивом варианте
   ошибка возникает только у того кода, который реально полез за ключом.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Каталог backend/ — на уровень выше пакета app/.
# Путь абсолютный намеренно: pydantic-settings ищет относительный env_file
# от текущего рабочего каталога, а pytest, alembic и scripts/natal_cli.py
# запускаются из разных мест. Тот же приём — в astro_core/geo.py для cities.txt.
BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Значения берутся из переменных окружения, затем из backend/.env.

    Окружение имеет приоритет над файлом — так в Docker Compose и на VPS
    секреты передаются напрямую, без монтирования .env внутрь контейнера.
    """

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        # Лишние переменные в .env (служебные, чужие, закомментированные
        # наспех) не должны ронять запуск — иначе каждый новый ключ в файле
        # требует правки этого класса.
        extra="ignore",
    )

    # --- Обязательное ---------------------------------------------------
    # SecretStr вместо str: значение не попадёт в repr(), в traceback
    # и в логи FastAPI при ошибке. Читать через .get_secret_value().
    deepseek_api_key: SecretStr

    # Строка подключения к Postgres в формате SQLAlchemy. Без дефолта
    # намеренно: дефолт вида "...@localhost/astro" означает, что забытая
    # переменная на сервере не роняет запуск, а тихо уводит приложение на
    # локальную базу, которой там нет (или, хуже, есть чужая). Обязательное
    # поле падает сразу и с понятным текстом.
    #
    # Драйвер обязателен в схеме: "postgresql+asyncpg://", а не просто
    # "postgresql://" — иначе SQLAlchemy подставит синхронный psycopg
    # и create_async_engine упадёт с невнятной InvalidRequestError.
    database_url: str

    # --- С безопасными дефолтами ----------------------------------------
    # Логировать ли каждый SQL-запрос (echo движка SQLAlchemy). Отдельный
    # флаг, а не debug: debug влияет на выдачу ошибок наружу, а поток SQL
    # в консоли нужен точечно и мешает даже в dev.
    db_echo: bool = False

    # Режим окружения: "dev" | "prod". Влияет на подробность ошибок и на то,
    # отдаём ли мы /docs наружу.
    app_env: str = "dev"
    debug: bool = False

    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"


@lru_cache
def get_settings() -> Settings:
    """Единственная точка доступа к настройкам.

    lru_cache даёт два эффекта: .env парсится один раз за процесс, и объект
    настроек везде один и тот же. В тестах кеш сбрасывается вызовом
    get_settings.cache_clear() — например, после monkeypatch переменных.
    """
    return Settings()
