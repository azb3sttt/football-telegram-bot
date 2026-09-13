from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    football_api_key: str
    football_api_base: str
    database_path: str
    timezone: str


settings = Settings(
    telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
    football_api_key=os.getenv("FOOTBALL_API_KEY", "").strip(),
    football_api_base=os.getenv(
        "FOOTBALL_API_BASE", "https://v3.football.api-sports.io"
    ).rstrip("/"),
    database_path=os.getenv("DATABASE_PATH", "/data/bot.sqlite3"),
    timezone=os.getenv("TIMEZONE", "Europe/Warsaw"),
)
