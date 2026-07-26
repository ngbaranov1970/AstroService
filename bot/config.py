from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict

# Единая таймзона приложения: по ней считаются «сегодня» для ключей кеша,
# даты в промптах LLM и расписание рассылки. Явная константа, а не время
# сервера — на VPS системная таймзона обычно UTC, и «день» сместился бы
# на 3 часа относительно аудитории бота.
TIMEZONE = ZoneInfo("Europe/Moscow")


class Settings(BaseSettings):
    deepseek_api_key: str
    deepseek_api_url: str
    deepseek_model: str

    bot_token: str
    # Redis не критичен для работы бота (см. HoroscopeService) — дефолт
    # рассчитан на локальный Redis из docker-compose, без правки .env.
    redis_url: str = "redis://localhost:6379/0"
    db_path: str = "bot.db"

    model_config = SettingsConfigDict(env_file=Path(__file__).parent / ".env")


settings = Settings()  # type: ignore[call-arg]
