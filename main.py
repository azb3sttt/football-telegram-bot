import logging

from telegram import BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters

from config import settings
from database import Database
from football_api import FootballAPI
from bot import (
    cmd_start, cmd_pomoc, cmd_status, cmd_dzisiaj, cmd_ligi, cmd_kupon, cmd_anuluj,
    cmd_profil, cmd_betbuilder, cmd_builder_market, builder_callback,
    coupon_target_message, cmd_wyniki, cmd_tydzien, cmd_miesiac, cmd_yield,
    cmd_historia, cmd_powiadomienia, notifications_callback,
    cmd_stawiam, cmd_niegram, cmd_kuponinfo, cmd_mojekupony, cmd_aktywny,
    cmd_proponowane, cmd_bilans, cmd_statystyki, cmd_seria, cmd_limit,
)
from scheduler import SchedulerService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("football-bot")


async def post_init(app: Application) -> None:
    db: Database = app.bot_data["db"]
    api: FootballAPI = app.bot_data["api"]

    await db.initialize()
    log.info("✅ Baza danych gotowa")

    try:
        await api.ping()
        log.info("✅ API piłkarskie dostępne")
    except Exception as exc:
        log.warning("⚠️ API piłkarskie chwilowo niedostępne: %s", exc)

    commands = [
        BotCommand("start", "Uruchom bota"),
        BotCommand("pomoc", "Lista komend"),
        BotCommand("dzisiaj", "Wybierz ligę / dzisiejsze mecze"),
        BotCommand("ligi", "Lista 20 głównych lig"),
        BotCommand("kupon", "Kupon z twardym celem kursu"),
        BotCommand("profil", "SAFE / NORMAL / RISKY"),
        BotCommand("betbuilder", "Bet Builder z jednego meczu"),
        BotCommand("builder", "Alias Bet Buildera"),
        BotCommand("anuluj", "Przerwij kreator kuponu"),
        BotCommand("stawiam", "Oznacz kupon jako faktycznie zagrany"),
        BotCommand("mojekupony", "Twoje postawione kupony"),
        BotCommand("aktywny", "Aktywne postawione kupony"),
        BotCommand("proponowane", "Niezagrane propozycje bota"),
        BotCommand("kuponinfo", "Szczegóły kuponu po ID"),
        BotCommand("bilans", "Bilans kuponów AKO i selekcji"),
        BotCommand("statystyki", "Pełne statystyki postawionych typów"),
        BotCommand("seria", "Aktualna seria wyników"),
        BotCommand("limit", "Pozostały limit Football API"),
        BotCommand("wyniki", "Ostatnie rozliczone postawione typy"),
        BotCommand("tydzien", "Raport z 7 dni"),
        BotCommand("miesiac", "Raport z miesiąca"),
        BotCommand("yield", "Statystyki yield"),
        BotCommand("historia", "Historia typów"),
        BotCommand("powiadomienia", "Ustawienia powiadomień"),
        BotCommand("status", "Status usług i limit API"),
    ]
    await app.bot.set_my_commands(commands)
    log.info("✅ Połączenie z Telegramem")


def main() -> None:
    if not settings.telegram_bot_token:
        raise RuntimeError("Brak TELEGRAM_BOT_TOKEN")
    if not settings.football_api_key:
        raise RuntimeError("Brak FOOTBALL_API_KEY")

    db = Database(settings.database_path)
    api = FootballAPI(settings.football_api_key)

    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(post_init)
        .build()
    )
    app.bot_data["db"] = db
    app.bot_data["api"] = api

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("pomoc", cmd_pomoc))
    app.add_handler(CommandHandler("dzisiaj", cmd_dzisiaj))
    app.add_handler(CommandHandler("mecze", cmd_dzisiaj))
    app.add_handler(CommandHandler("ligi", cmd_ligi))
    app.add_handler(CommandHandler("liga", cmd_ligi))
    app.add_handler(CommandHandler("kupon", cmd_kupon))
    app.add_handler(CommandHandler("profil", cmd_profil))
    app.add_handler(CommandHandler("betbuilder", cmd_betbuilder))
    app.add_handler(CommandHandler("builder", cmd_betbuilder))
    app.add_handler(CommandHandler("builder_mecz", cmd_betbuilder))
    app.add_handler(CommandHandler("builder_gole", cmd_builder_market))
    app.add_handler(CommandHandler("builder_rogi", cmd_builder_market))
    app.add_handler(CommandHandler("builder_kartki", cmd_builder_market))
    app.add_handler(CommandHandler("builder_strzaly", cmd_builder_market))
    app.add_handler(CommandHandler("builder_celne", cmd_builder_market))
    app.add_handler(CommandHandler("builder_faule", cmd_builder_market))
    app.add_handler(CommandHandler("anuluj", cmd_anuluj))
    app.add_handler(CommandHandler("stawiam", cmd_stawiam))
    app.add_handler(CommandHandler("niegram", cmd_niegram))
    app.add_handler(CommandHandler("kuponinfo", cmd_kuponinfo))
    app.add_handler(CommandHandler("mojekupony", cmd_mojekupony))
    app.add_handler(CommandHandler("aktywny", cmd_aktywny))
    app.add_handler(CommandHandler("proponowane", cmd_proponowane))
    app.add_handler(CommandHandler("bilans", cmd_bilans))
    app.add_handler(CommandHandler("statystyki", cmd_statystyki))
    app.add_handler(CommandHandler("statystyki_bota", cmd_statystyki))
    app.add_handler(CommandHandler("seria", cmd_seria))
    app.add_handler(CommandHandler("limit", cmd_limit))
    app.add_handler(CommandHandler("wyniki", cmd_wyniki))
    app.add_handler(CommandHandler("dzisiaj_wyniki", cmd_wyniki))
    app.add_handler(CommandHandler("tydzien", cmd_tydzien))
    app.add_handler(CommandHandler("miesiac", cmd_miesiac))
    app.add_handler(CommandHandler("yield", cmd_yield))
    app.add_handler(CommandHandler("roi", cmd_yield))
    app.add_handler(CommandHandler("historia", cmd_historia))
    app.add_handler(CommandHandler("powiadomienia", cmd_powiadomienia))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CallbackQueryHandler(notifications_callback, pattern=r"^notif:"))
    app.add_handler(CallbackQueryHandler(builder_callback, pattern=r"^bb:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, coupon_target_message))

    scheduler = SchedulerService(app, db, api)
    app.bot_data["scheduler"] = scheduler
    scheduler.install_jobs()
    log.info("✅ Scheduler uruchomiony")
    log.info("✅ Bot działa")

    app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
