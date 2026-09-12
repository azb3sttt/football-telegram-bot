from __future__ import annotations

import argparse
import asyncio
import importlib.util
import sys

from app.config import get_settings
from app.logging_config import configure_logging


async def self_check() -> int:
    # Ten check celowo nie łączy się z Telegramem ani API-Football.
    # Sprawdza składnię/importy modułów rdzenia i schemat bazy w pamięci.
    from sqlalchemy import create_engine

    from app.db.base import Base
    from app.db import models  # noqa: F401
    from app.services import analysis, reports, settlement  # noqa: F401

    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()

    print("✅ Importy rdzenia działają")
    print("✅ Schemat bazy danych może zostać utworzony")
    print("✅ Logika analiz, raportów i settlementu jest dostępna")
    if importlib.util.find_spec("telegram") is None:
        print("⚠️ python-telegram-bot nie jest zainstalowany w tym środowisku")
    else:
        import app.bot  # noqa: F401
        print("✅ Import warstwy Telegram działa")
    if importlib.util.find_spec("aiosqlite") is None:
        print("⚠️ aiosqlite nie jest zainstalowany w tym środowisku")
    else:
        from app.bot import build_services

        settings = get_settings()
        services, db = build_services(settings)
        try:
            await db.create_all()
            print("✅ Asynchroniczna baza SQLite działa")
        finally:
            await services.api.close()
            await db.close()
    print("✅ Tryb testowy nie wymaga prawdziwego tokena ani klucza API")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Football Telegram Bot")
    parser.add_argument("--check", action="store_true", help="Sprawdź start lokalnie bez zewnętrznych API")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)
    if args.check:
        raise SystemExit(asyncio.run(self_check()))
    if not settings.telegram_configured:
        print("❌ Brak TELEGRAM_BOT_TOKEN w pliku .env")
        sys.exit(2)

    from app.bot import create_application

    app = create_application(settings)
    app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
