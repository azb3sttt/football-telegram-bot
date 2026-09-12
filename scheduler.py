from __future__ import annotations

import logging
from datetime import datetime, timedelta

from telegram.ext import ContextTypes

from app.services.settlement import FINISHED_STATUSES, settle_selection
from app.utils.formatters import format_coupon_summary, format_selection_result

logger = logging.getLogger(__name__)

STAT_MARKETS = {
    "corners_total",
    "total_corners",
    "rogi",
    "rzuty_rozne",
    "rzuty_rożne",
    "corners_team",
    "team_corners",
    "cards_total",
    "total_cards",
    "kartki",
    "cards_team",
    "team_cards",
}


def _services(context: ContextTypes.DEFAULT_TYPE):
    return context.application.bot_data["services"]


async def check_pending_results(context: ContextTypes.DEFAULT_TYPE) -> None:
    s = _services(context)
    ids = await s.repo.pending_fixture_ids()
    if not ids or not s.settings.football_api_configured:
        return
    try:
        fixtures = await s.api.fixtures_batch(ids, ttl=40)
    except Exception:
        logger.exception("Nie udało się odświeżyć oczekujących wyników")
        return

    by_id = {int(x["fixture"]["id"]): x for x in fixtures}
    notified_coupons: set[int] = set()
    for fixture_id in ids:
        fixture = by_id.get(fixture_id)
        if not fixture:
            continue
        await s.repo.upsert_fixture(fixture)
        status_short = fixture.get("fixture", {}).get("status", {}).get("short", "NS")
        selections = await s.repo.pending_selections_for_fixture(fixture_id)
        needs_stats = status_short in FINISHED_STATUSES and any(sel.market in STAT_MARKETS for sel in selections)
        raw_stats = []
        if needs_stats:
            try:
                raw_stats = await s.api.fixture_statistics(fixture_id, ttl=90)
            except Exception:
                logger.exception("Nie udało się pobrać statystyk fixture=%s", fixture_id)

        goals = fixture.get("goals", {})
        score = f"{goals.get('home', '-')}:{goals.get('away', '-')}"
        for selection in selections:
            settlement = settle_selection(selection, fixture, raw_stats)
            if settlement.status == "pending":
                continue
            settled = await s.repo.settle_selection(
                selection.id,
                settlement.status,
                settlement.final_result,
                settlement.profit_loss,
            )
            coupon = await s.repo.recalculate_coupon(selection.coupon_id)
            prefs = await s.repo.get_notification_settings(selection.user_id)
            if prefs.selection_result:
                finished = sum(x.status != "pending" for x in coupon.selections)
                total = len(coupon.selections)
                text = format_selection_result(settled, score) + f"\n\nKupon: {finished}/{total} typów zakończonych."
                try:
                    await context.bot.send_message(selection.user_id, text)
                except Exception:
                    logger.exception("Nie udało się wysłać wyniku typu użytkownikowi %s", selection.user_id)
            if coupon.status != "pending" and coupon.id not in notified_coupons and prefs.coupon_result:
                notified_coupons.add(coupon.id)
                try:
                    await context.bot.send_message(selection.user_id, format_coupon_summary(coupon))
                except Exception:
                    logger.exception("Nie udało się wysłać podsumowania kuponu %s", coupon.id)


async def send_daily_reports(context: ContextTypes.DEFAULT_TYPE) -> None:
    s = _services(context)
    for user_id in await s.repo.notification_user_ids("daily_report"):
        start, end, rows = await s.reports.daily(user_id)
        if not rows:
            continue
        from app.services.reports import format_metrics_report

        try:
            await context.bot.send_message(user_id, format_metrics_report("📊 PODSUMOWANIE DNIA", start, end, rows))
        except Exception:
            logger.exception("Nie udało się wysłać raportu dziennego do %s", user_id)


async def send_weekly_reports(context: ContextTypes.DEFAULT_TYPE) -> None:
    s = _services(context)
    now = datetime.now(s.settings.tz)
    if now.weekday() != 6:  # niedziela
        return
    from app.services.reports import format_metrics_report

    for user_id in await s.repo.notification_user_ids("weekly_report"):
        start, end, rows = await s.reports.last_days(user_id, 7)
        if not rows:
            continue
        try:
            await context.bot.send_message(user_id, format_metrics_report("📊 RAPORT TYGODNIOWY", start, end, rows))
        except Exception:
            logger.exception("Nie udało się wysłać raportu tygodniowego do %s", user_id)


async def send_monthly_reports(context: ContextTypes.DEFAULT_TYPE) -> None:
    s = _services(context)
    now = datetime.now(s.settings.tz)
    if now.day != 1:
        return
    from app.services.reports import format_metrics_report

    months = {
        1: "STYCZNIA", 2: "LUTEGO", 3: "MARCA", 4: "KWIETNIA", 5: "MAJA", 6: "CZERWCA",
        7: "LIPCA", 8: "SIERPNIA", 9: "WRZEŚNIA", 10: "PAŹDZIERNIKA", 11: "LISTOPADA", 12: "GRUDNIA",
    }
    for user_id in await s.repo.notification_user_ids("monthly_report"):
        start, end, rows = await s.reports.month(user_id, previous=True)
        if not rows:
            continue
        title = f"📅 PODSUMOWANIE {months[start.month]} {start.year}"
        try:
            await context.bot.send_message(user_id, format_metrics_report(title, start, end, rows))
        except Exception:
            logger.exception("Nie udało się wysłać raportu miesięcznego do %s", user_id)
