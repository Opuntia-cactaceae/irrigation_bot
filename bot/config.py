# bot/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # === BOT ===
    BOT_TOKEN: str

    # === PROXY ===
    PROXY_URL: str

    # === DATABASE ===
    DATABASE_URL: str                # async для SQLAlchemy
    DATABASE_URL_SYNC: str           # sync для Alembic и APScheduler

    # === APP ===
    TIMEZONE_DEFAULT: str = "Europe/Amsterdam"

    # === APSCHEDULER ===
    APSCHEDULER_TABLE: str = "apscheduler_jobs"

    # === LOGGING ===
    LOG_LEVEL: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # чтобы не падало, если есть лишние переменные
    )


settings = Settings()