from __future__ import annotations

import logging
from datetime import datetime, time

from telegram import BotCommand, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from app import commands
from app.config import Settings
from app.db.base import Database
from app.db.repository import Repository
from app.runtime import Services
from app.scheduler import check_pending_results, send_daily_reports, send_monthly_reports, send_weekly_reports
from app.services.analysis import MatchAnalyzer
from app.services.cache import DiskJsonCache
from app.services.football_api import FootballApiClient
from app.services.league_catalog import LeagueCatalog
from app.services.reports import ReportService

logger = logging.getLogger(__name__)

BOT_COMMANDS = [
    BotCommand("start", "Uruchom bota"),
    BotCommand("pomoc", "Lista komend"),
    BotCommand("dzisiaj", "Dzisiejsze mecze"),
    BotCommand("mecze", "Dzisiejsze mecze"),
    BotCommand("analiza", "Analiza meczu po ID"),
    BotCommand("kupon", "Zbuduj kupon pod kurs"),
    BotCommand("value", "Dzisiejsze value"),
    BotCommand("top", "Najlepsze dzisiejsze typy"),
    BotCommand("liga", "Popularne ligi"),
    BotCommand("druzyna", "Znajdź dzisiejszy mecz drużyny"),
    BotCommand("h2h", "Ostatnie H2H"),
    BotCommand("rogi", "Rzuty rożne meczu"),
    BotCommand("gole", "Analiza goli"),
    BotCommand("kartki", "Kartki w meczu"),
    BotCommand("btts", "Analiza BTTS"),
    BotCommand("wyniki", "Ostatnie wyniki typów"),
    BotCommand("dzisiaj_wyniki", "Dzisiejsze wyniki"),
    BotCommand("tydzien", "Raport 7 dni"),
    BotCommand("miesiac", "Raport miesiąca"),
    BotCommand("yield", "Yield według kategorii"),
    BotCommand("roi", "ROI z 30 dni"),
    BotCommand("historia", "Historia typów"),
    BotCommand("statystyki_bota", "Statystyki 30 dni"),
    BotCommand("powiadomienia", "Ustaw powiadomienia"),
    BotCommand("ustawienia", "Ustawienia użytkownika"),
    BotCommand("status", "Status usług"),
]


def build_services(settings: Settings) -> tuple[Services, Database]:
    db = Database(settings.database_url)
    repo = Repository(db.session_factory)
    api = FootballApiClient(settings.football_api_key, settings.football_api_base_url, DiskJsonCache("cache"))
    analyzer = MatchAnalyzer(api)
    reports = ReportService(repo, settings.timezone)
    leagues = LeagueCatalog(api)
    return Services(settings, repo, api, analyzer, reports, leagues), db


async def post_init(application: Application) -> None:
    s: Services = application.bot_data["services"]
    db: Database = application.bot_data["database"]

    await db.create_all()
    logger.info("✅ Baza danych gotowa")
    me = await application.bot.get_me()
    logger.info("✅ Połączenie z Telegramem (@%s)", me.username)
    await application.bot.set_my_commands(BOT_COMMANDS)

    api_ok = False
    if s.settings.football_api_configured:
        api_ok = await s.api.health_check()
    if api_ok:
        logger.info("✅ API piłkarskie dostępne")
        try:
            await s.leagues.sync()
            fixtures = await s.api.fixtures_by_date(datetime.now(s.settings.tz).date(), s.settings.timezone)
            for fixture in fixtures:
                await s.repo.upsert_fixture(fixture)
        except Exception:
            logger.exception("Synchronizacja lig lub meczów startowych nie powiodła się")
    else:
        logger.warning("⚠️ API piłkarskie niedostępne lub brak FOOTBALL_API_KEY")

    jq = application.job_queue
    if jq is None:
        raise RuntimeError("JobQueue nie jest dostępny. Zainstaluj python-telegram-bot[job-queue].")
    jq.run_repeating(
        check_pending_results,
        interval=s.settings.result_check_interval_seconds,
        first=15,
        name="sprawdzanie-wynikow",
    )
    jq.run_daily(
        send_daily_reports,
        time=time(s.settings.daily_report_hour, s.settings.daily_report_minute, tzinfo=s.settings.tz),
        name="raport-dzienny",
    )
    # Te dwa joby uruchamiają się codziennie o wskazanej godzinie, a same funkcje sprawdzają dzień tygodnia/miesiąca.
    jq.run_daily(
        send_weekly_reports,
        time=time(s.settings.weekly_report_hour, s.settings.weekly_report_minute, tzinfo=s.settings.tz),
        name="raport-tygodniowy",
    )
    jq.run_daily(
        send_monthly_reports,
        time=time(s.settings.monthly_report_hour, s.settings.monthly_report_minute, tzinfo=s.settings.tz),
        name="raport-miesieczny",
    )
    logger.info("✅ Scheduler uruchomiony")
    logger.info("✅ Bot działa")


async def post_shutdown(application: Application) -> None:
    s: Services = application.bot_data["services"]
    db: Database = application.bot_data["database"]
    await s.api.close()
    await db.close()


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Błąd obsługi aktualizacji", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "Wystąpił błąd podczas obsługi tej komendy. Spróbuj ponownie za chwilę."
            )
        except Exception:
            pass


def create_application(settings: Settings) -> Application:
    services, db = build_services(settings)
    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.bot_data["services"] = services
    app.bot_data["database"] = db

    handlers = {
        "start": commands.start,
        "pomoc": commands.pomoc,
        "dzisiaj": commands.dzisiaj,
        "mecze": commands.dzisiaj,
        "analiza": commands.analiza,
        "kupon": commands.kupon,
        "value": commands.value,
        "top": commands.top,
        "liga": commands.liga,
        "druzyna": commands.druzyna,
        "h2h": commands.h2h,
        "rogi": commands.rogi,
        "gole": commands.gole,
        "kartki": commands.kartki,
        "btts": commands.btts,
        "wyniki": commands.wyniki,
        "dzisiaj_wyniki": commands.dzisiaj_wyniki,
        "tydzien": commands.tydzien,
        "miesiac": commands.miesiac,
        "yield": commands.yield_cmd,
        "roi": commands.roi,
        "historia": commands.historia,
        "statystyki_bota": commands.statystyki_bota,
        "powiadomienia": commands.powiadomienia,
        "ustawienia": commands.ustawienia,
        "status": commands.status,
    }
    for name, callback in handlers.items():
        app.add_handler(CommandHandler(name, callback))
    app.add_handler(CallbackQueryHandler(commands.notification_callback, pattern=r"^notif:"))
    app.add_error_handler(error_handler)
    return app
