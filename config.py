from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    football_api_key: str = Field(default="", alias="FOOTBALL_API_KEY")
    database_url: str = Field(default="sqlite+aiosqlite:///./football_bot.sqlite", alias="DATABASE_URL")
    football_api_base_url: str = Field(default="https://v3.football.api-sports.io", alias="FOOTBALL_API_BASE_URL")
    timezone: str = Field(default="Europe/Warsaw", alias="TIMEZONE")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    default_unit: float = Field(default=1.0, alias="DEFAULT_UNIT", gt=0)
    result_check_interval_seconds: int = Field(default=180, alias="RESULT_CHECK_INTERVAL_SECONDS", ge=60)
    daily_report_hour: int = Field(default=23, alias="DAILY_REPORT_HOUR", ge=0, le=23)
    daily_report_minute: int = Field(default=10, alias="DAILY_REPORT_MINUTE", ge=0, le=59)
    weekly_report_hour: int = Field(default=20, alias="WEEKLY_REPORT_HOUR", ge=0, le=23)
    weekly_report_minute: int = Field(default=30, alias="WEEKLY_REPORT_MINUTE", ge=0, le=59)
    monthly_report_hour: int = Field(default=9, alias="MONTHLY_REPORT_HOUR", ge=0, le=23)
    monthly_report_minute: int = Field(default=0, alias="MONTHLY_REPORT_MINUTE", ge=0, le=59)
    dry_run: bool = Field(default=False, alias="DRY_RUN")

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_bot_token.strip())

    @property
    def football_api_configured(self) -> bool:
        return bool(self.football_api_key.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
