import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from config import settings
from settlement import settle
from reports import format_report

log = logging.getLogger("scheduler")
PL = ZoneInfo(settings.timezone)


class SchedulerService:
    def __init__(self, app, db, api):
        self.app = app
        self.db = db
        self.api = api

    def install_jobs(self):
        jq = self.app.job_queue
        jq.run_repeating(self.check_results, interval=180, first=20, name="wyniki")
        jq.run_daily(self.daily_report, time=datetime.strptime("23:10", "%H:%M").time(), name="raport-dzienny")
        jq.run_daily(self.weekly_report, time=datetime.strptime("21:00", "%H:%M").time(), days=(6,), name="raport-tygodniowy")
        jq.run_monthly(self.monthly_report, when=datetime.strptime("20:30", "%H:%M").time(), day=1, name="raport-miesieczny")

    async def check_results(self, context):
        rows = await self.db.pending_selections()
        fixture_ids = sorted({int(r["fixture_id"]) for r in rows})
        for fixture_id in fixture_ids:
            try:
                fixture = await self.api.fixture(fixture_id)
                if not fixture:
                    continue
                status = (((fixture.get("fixture") or {}).get("status") or {}).get("short") or "").upper()
                if status not in {"FT","AET","PEN","PST","CANC","ABD","AWD","WO"}:
                    continue
                stats = await self.api.fixture_statistics(fixture_id) if status in {"FT","AET","PEN"} else []
                selections = await self.db.fixture_selections(fixture_id)
                for row in selections:
                    result = settle(row, fixture, stats)
                    if result.status == "pending":
                        continue
                    await self.db.settle_selection(row["id"], result.status, result.final_result, result.profit_loss)
                    coupon, _ = await self.db.get_coupon(row["user_id"], row["coupon_id"])
                    user = await self.db.get_notification_settings(row["user_id"])
                    if coupon and coupon["is_placed"] and user and user["notify_selection"]:
                        icon = {"won":"✅","lost":"❌","push":"↩️","void":"🚫"}[result.status]
                        await context.bot.send_message(
                            row["user_id"],
                            f"{icon} MECZ ZAKOŃCZONY\n\n"
                            f"{row['home_team'] or ''} – {row['away_team'] or ''}\n\n"
                            f"Twój typ:\n{row['selection']}\n\n"
                            f"Wynik:\n{result.final_result}\n\n"
                            f"{icon} {result.status.upper()}\n"
                            f"Kurs: {float(row['odds']):.2f}\n"
                            f"Kupon: #{row['coupon_id']}"
                        )
                    refreshed = await self.db.refresh_coupon(row["coupon_id"])
                    if (
                        refreshed and refreshed["is_placed"] and refreshed["status"] != "pending"
                        and user and user["notify_coupon"]
                    ):
                        # Wyślij podsumowanie tylko gdy właśnie domknęła się ostatnia selekcja.
                        coupon2, all_picks = await self.db.get_coupon(row["user_id"], row["coupon_id"])
                        if all(p["status"] != "pending" for p in all_picks):
                            icon2 = {"won":"✅","lost":"❌","push":"↩️","void":"🚫"}.get(coupon2["status"], "•")
                            lines = [
                                f"🎟 PODSUMOWANIE KUPONU #{coupon2['coupon_id']}",
                                "",
                            ]
                            for i, p in enumerate(all_picks, 1):
                                pi = {"won":"✅","lost":"❌","push":"↩️","void":"🚫"}.get(p["status"], "•")
                                lines.append(f"{i}. {pi} {p['selection']} @ {float(p['odds']):.2f}")
                            lines += [
                                "",
                                f"Łączny kurs: {float(coupon2['combined_odds'] or 0):.2f}",
                                f"Stawka: {float(coupon2['stake_units'] or 1):.2f} units",
                                f"{icon2} Wynik: {coupon2['status'].upper()}",
                                f"Profit/Loss: {float(coupon2['profit_loss'] or 0):+.2f} units",
                            ]
                            await context.bot.send_message(row["user_id"], "\n".join(lines))
            except Exception as exc:
                log.exception("Błąd sprawdzania fixture %s: %s", fixture_id, exc)

    async def _send_report_to_users(self, context, since_iso, title, flag):
        db = await self.db.connect()
        try:
            cur = await db.execute(f"SELECT user_id FROM users WHERE {flag}=1")
            users = await cur.fetchall()
        finally:
            await db.close()
        for u in users:
            rows = await self.db.report_rows(u["user_id"], since_iso)
            try:
                await context.bot.send_message(u["user_id"], format_report(title, rows))
            except Exception:
                pass

    async def daily_report(self, context):
        now = datetime.now(PL)
        since = now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(ZoneInfo("UTC")).isoformat()
        await self._send_report_to_users(context, since, "PODSUMOWANIE DNIA", "notify_daily")

    async def weekly_report(self, context):
        since = (datetime.now(PL) - timedelta(days=7)).astimezone(ZoneInfo("UTC")).isoformat()
        await self._send_report_to_users(context, since, "RAPORT TYGODNIOWY", "notify_weekly")

    async def monthly_report(self, context):
        now = datetime.now(PL)
        first_this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_prev = first_this - timedelta(seconds=1)
        first_prev = last_prev.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        await self._send_report_to_users(
            context,
            first_prev.astimezone(ZoneInfo("UTC")).isoformat(),
            f"PODSUMOWANIE {last_prev.strftime('%m/%Y')}",
            "notify_monthly"
        )
