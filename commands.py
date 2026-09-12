from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.keyboards import notifications_keyboard
from app.services.analysis import MatchAnalyzer
from app.services.football_api import FootballApiError
from app.services.reports import (
    ReportService,
    calculate_metrics,
    format_metrics_report,
    format_yield_breakdown,
)
from app.utils.formatters import STATUS_EMOJI, format_candidate, format_coupon_summary, pick_label

logger = logging.getLogger(__name__)

HELP_TEXT = """🤖 FOOTBALL BOT — POMOC

Najważniejsze komendy:
/dzisiaj — dzisiejsze mecze
/analiza ID — analiza konkretnego meczu
/top — najlepsze dzisiejsze value
/kupon 5.0 — zbuduj kupon w okolicy kursu 5.0
/value — typy z dodatnim edge
/h2h ID — ostatnie bezpośrednie mecze
/gole ID — analiza rynku goli
/btts ID — analiza BTTS
/rogi ID — końcowe/statystyczne dane o rożnych, jeśli dostępne
/kartki ID — końcowe/statystyczne dane o kartkach, jeśli dostępne

Wyniki i raporty:
/wyniki — ostatnie rozliczone typy
/dzisiaj_wyniki — wyniki dzisiaj
/tydzien — ostatnie 7 dni
/miesiac — bieżący miesiąc
/yield — rozbicie wyników
/roi — ROI
/historia — historia typów
/statystyki_bota — ostatnie 30 dni

Ustawienia:
/powiadomienia — włącz/wyłącz alerty
/ustawienia — jednostka i ustawienia
/status — stan usług

Bot prowadzi wirtualny tracking w units. Nie zwiększa stawek po przegranej."""


def _services(context: ContextTypes.DEFAULT_TYPE):
    return context.application.bot_data["services"]


async def _reply_chunks(update: Update, text: str) -> None:
    message = update.effective_message
    if not message:
        return
    max_len = 3900
    chunks = []
    current = ""
    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) > max_len and current:
            chunks.append(current)
            current = block
        else:
            current = candidate
    if current:
        chunks.append(current)
    for chunk in chunks:
        await message.reply_text(chunk)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = _services(context)
    user = update.effective_user
    if user:
        await s.repo.ensure_user(user.id, user.username, s.settings.default_unit)
    await update.effective_message.reply_text(
        "⚽ Bot jest gotowy.\n\n"
        "Najpierw wpisz /dzisiaj, potem możesz użyć /analiza ID albo /kupon 5.0.\n"
        "Pełna lista: /pomoc"
    )


async def pomoc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(HELP_TEXT)


async def dzisiaj(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = _services(context)
    try:
        rows = await s.api.fixtures_by_date(datetime.now(s.settings.tz).date(), s.settings.timezone)
    except FootballApiError:
        await update.effective_message.reply_text(
            "Nie udało się pobrać danych. Spróbuję ponownie przy następnym odświeżeniu."
        )
        return
    for row in rows:
        await s.repo.upsert_fixture(row)
    league_ids = s.leagues.ids()
    popular = [r for r in rows if not league_ids or r.get("league", {}).get("id") in league_ids]
    popular.sort(key=lambda r: r.get("fixture", {}).get("timestamp") or 0)
    if not popular:
        await update.effective_message.reply_text("Nie znaleziono dzisiejszych spotkań.")
        return
    lines = ["📅 DZISIEJSZE MECZE", ""]
    for row in popular[:40]:
        fixture = row["fixture"]
        dt = datetime.fromisoformat(fixture["date"]).astimezone(s.settings.tz)
        lines.append(
            f"{dt:%H:%M} | ID {fixture['id']} | {row['teams']['home']['name']} – {row['teams']['away']['name']} "
            f"[{row.get('league', {}).get('name', '-')}]"
        )
    lines += ["", "Analiza: /analiza ID"]
    await _reply_chunks(update, "\n".join(lines))


async def _fixture_from_arg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> dict | None:
    if not context.args:
        await update.effective_message.reply_text("Podaj ID meczu, np. /analiza 1234567")
        return None
    try:
        fixture_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("ID meczu musi być liczbą.")
        return None
    s = _services(context)
    try:
        fixture = await s.api.fixture(fixture_id, live_ttl=60)
    except FootballApiError:
        await update.effective_message.reply_text("Nie udało się pobrać danych meczu.")
        return None
    if not fixture:
        await update.effective_message.reply_text("Nie znaleziono meczu o takim ID.")
        return None
    await s.repo.upsert_fixture(fixture)
    return fixture


async def analiza(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    fixture = await _fixture_from_arg(update, context)
    if not fixture:
        return
    s = _services(context)
    try:
        picks = await s.analyzer.analyze_fixture(fixture)
    except FootballApiError:
        await update.effective_message.reply_text("Nie udało się pobrać pełnych danych do analizy.")
        return
    title = f"🔎 ANALIZA\n{fixture['teams']['home']['name']} – {fixture['teams']['away']['name']}\n"
    if not picks:
        await update.effective_message.reply_text(
            title + "\nModel nie znalazł obecnie typu spełniającego minimalny confidence i dodatni edge albo API nie udostępnia kursów."
        )
        return
    body = "\n\n".join(format_candidate(p, i) for i, p in enumerate(picks[:5], 1))
    await _reply_chunks(update, title + "\n" + body)


async def top(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = _services(context)
    try:
        fixtures = await s.api.fixtures_by_date(datetime.now(s.settings.tz).date(), s.settings.timezone)
        picks = await s.analyzer.best_today(fixtures, s.leagues.ids(), max_fixtures=8)
    except FootballApiError:
        await update.effective_message.reply_text("Nie udało się pobrać danych do rankingu.")
        return
    if not picks:
        await update.effective_message.reply_text("Dzisiaj nie znalazłem value spełniającego ustawione progi.")
        return
    await _reply_chunks(update, "⭐ TOP VALUE\n\n" + "\n\n".join(format_candidate(p, i) for i, p in enumerate(picks[:8], 1)))


async def value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await top(update, context)


async def kupon(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = _services(context)
    user = update.effective_user
    if not user:
        return
    try:
        target = float(context.args[0].replace(",", ".")) if context.args else 5.0
    except ValueError:
        await update.effective_message.reply_text("Podaj kurs jako liczbę, np. /kupon 5.0")
        return
    target = min(max(target, 1.5), 30.0)
    await s.repo.ensure_user(user.id, user.username, s.settings.default_unit)
    try:
        fixtures = await s.api.fixtures_by_date(datetime.now(s.settings.tz).date(), s.settings.timezone)
        for fixture in fixtures:
            await s.repo.upsert_fixture(fixture)
        candidates = await s.analyzer.best_today(fixtures, s.leagues.ids(), max_fixtures=10)
    except FootballApiError:
        await update.effective_message.reply_text("Nie udało się pobrać danych do zbudowania kuponu.")
        return
    selected = MatchAnalyzer.build_coupon(candidates, target)
    if not selected:
        await update.effective_message.reply_text("Nie znalazłem dziś wystarczająco dobrych, niepowtarzających się selekcji do kuponu.")
        return
    coupon = await s.repo.create_coupon(user.id, [p.as_dict() for p in selected], target_odds=target)
    lines = [f"🎟 KUPON #{coupon.id}", f"Cel kursu: ~{target:.2f}", f"Łączny kurs: {coupon.total_odds:.2f}", ""]
    for i, p in enumerate(selected, 1):
        lines.append(format_candidate(p, i))
        lines.append("")
    lines += [
        "Kupon zapisany w bazie. Bot będzie automatycznie sprawdzał jego wynik.",
        "Tracking jest w units; domyślnie 1 unit na typ i osobno 1 unit na kupon AKO.",
    ]
    await _reply_chunks(update, "\n".join(lines))


async def h2h(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    fixture = await _fixture_from_arg(update, context)
    if not fixture:
        return
    s = _services(context)
    h = fixture["teams"]["home"]
    a = fixture["teams"]["away"]
    try:
        rows = await s.api.head_to_head(h["id"], a["id"], 5)
    except FootballApiError:
        await update.effective_message.reply_text("Nie udało się pobrać H2H.")
        return
    lines = [f"🤝 H2H: {h['name']} – {a['name']}", ""]
    for row in rows:
        date = datetime.fromisoformat(row["fixture"]["date"]).astimezone(s.settings.tz)
        lines.append(f"{date:%d.%m.%Y}: {row['teams']['home']['name']} {row['goals']['home']}:{row['goals']['away']} {row['teams']['away']['name']}")
    await update.effective_message.reply_text("\n".join(lines) if rows else "Brak danych H2H.")


async def gole(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    fixture = await _fixture_from_arg(update, context)
    if not fixture:
        return
    s = _services(context)
    picks = await s.analyzer.analyze_fixture(fixture)
    rows = [p for p in picks if p.market in {"total_goals", "team_goals"}]
    if not rows:
        await update.effective_message.reply_text("Model nie znalazł obecnie value na rynku goli.")
        return
    await _reply_chunks(update, "⚽ ANALIZA GOLI\n\n" + "\n\n".join(format_candidate(p, i) for i, p in enumerate(rows, 1)))


async def btts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    fixture = await _fixture_from_arg(update, context)
    if not fixture:
        return
    s = _services(context)
    picks = await s.analyzer.analyze_fixture(fixture)
    rows = [p for p in picks if p.market == "btts"]
    if not rows:
        await update.effective_message.reply_text("Model nie znalazł obecnie value dla BTTS.")
        return
    await update.effective_message.reply_text("⚽ BTTS\n\n" + format_candidate(rows[0]))


async def _fixture_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE, stat_name: str, title: str) -> None:
    fixture = await _fixture_from_arg(update, context)
    if not fixture:
        return
    s = _services(context)
    try:
        raw = await s.api.fixture_statistics(fixture["fixture"]["id"], ttl=120)
    except FootballApiError:
        await update.effective_message.reply_text("Nie udało się pobrać statystyk meczu.")
        return
    values = []
    for row in raw:
        value = next((x.get("value") for x in row.get("statistics", []) if x.get("type") == stat_name), None)
        values.append((row.get("team", {}).get("name", "?"), value))
    if not values:
        await update.effective_message.reply_text("Te statystyki nie są obecnie dostępne w API dla tego meczu.")
        return
    await update.effective_message.reply_text(title + "\n\n" + "\n".join(f"{team}: {value if value is not None else '-'}" for team, value in values))


async def rogi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _fixture_stats_command(update, context, "Corner Kicks", "🚩 RZUTY ROŻNE")


async def kartki(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    fixture = await _fixture_from_arg(update, context)
    if not fixture:
        return
    s = _services(context)
    try:
        raw = await s.api.fixture_statistics(fixture["fixture"]["id"], ttl=120)
    except FootballApiError:
        await update.effective_message.reply_text("Nie udało się pobrać statystyk kartek.")
        return
    lines = ["🟨 KARTKI", ""]
    for row in raw:
        stats = {x.get("type"): x.get("value") for x in row.get("statistics", [])}
        lines.append(f"{row.get('team', {}).get('name', '?')}: żółte {stats.get('Yellow Cards', '-')}, czerwone {stats.get('Red Cards', '-')}")
    await update.effective_message.reply_text("\n".join(lines) if len(lines) > 2 else "Brak danych o kartkach.")


async def wyniki(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = _services(context)
    user = update.effective_user
    if not user:
        return
    rows = await s.repo.latest_settled(user.id, 12)
    if not rows:
        await update.effective_message.reply_text("Nie masz jeszcze zakończonych typów.")
        return
    lines = ["📋 OSTATNIE WYNIKI", ""]
    for r in rows:
        lines.append(
            f"{STATUS_EMOJI.get(r.status, r.status)} | {r.home_team} – {r.away_team} | "
            f"{pick_label(r.market, r.selection, r.line)} @ {r.odds:.2f} | P/L {float(r.profit_loss or 0):+.2f}u"
        )
    await _reply_chunks(update, "\n".join(lines))


async def dzisiaj_wyniki(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = _services(context)
    user = update.effective_user
    if not user:
        return
    start, end, rows = await s.reports.daily(user.id)
    await update.effective_message.reply_text(format_metrics_report("📊 PODSUMOWANIE DNIA", start, end, rows))


async def tydzien(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = _services(context)
    user = update.effective_user
    if not user:
        return
    start, end, rows = await s.reports.last_days(user.id, 7)
    await _reply_chunks(update, format_metrics_report("📊 RAPORT — OSTATNIE 7 DNI", start, end, rows))


async def miesiac(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = _services(context)
    user = update.effective_user
    if not user:
        return
    start, end, rows = await s.reports.month(user.id)
    await _reply_chunks(update, format_metrics_report(f"📅 PODSUMOWANIE {start:%m.%Y}", start, end, rows))


async def yield_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = _services(context)
    user = update.effective_user
    if not user:
        return
    _, _, rows = await s.reports.last_days(user.id, 30)
    await _reply_chunks(update, format_yield_breakdown(rows))


async def roi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = _services(context)
    user = update.effective_user
    if not user:
        return
    start, end, rows = await s.reports.last_days(user.id, 30)
    metrics = calculate_metrics(rows)
    await update.effective_message.reply_text(
        f"📈 ROI — OSTATNIE 30 DNI\n\nROI: {metrics.roi_pct:+.2f}%\nProfit: {metrics.profit:+.2f} units\nStawki: {metrics.total_staked:.2f} units"
    )


async def historia(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await wyniki(update, context)


async def statystyki_bota(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = _services(context)
    user = update.effective_user
    if not user:
        return
    start, end, rows = await s.reports.last_days(user.id, 30)
    await _reply_chunks(update, format_metrics_report("📊 OSTATNIE 30 DNI", start, end, rows))


async def powiadomienia(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = _services(context)
    user = update.effective_user
    if not user:
        return
    await s.repo.ensure_user(user.id, user.username, s.settings.default_unit)
    settings = await s.repo.get_notification_settings(user.id)
    await update.effective_message.reply_text(
        "🔔 POWIADOMIENIA\nKliknij, aby włączyć lub wyłączyć:",
        reply_markup=notifications_keyboard(settings),
    )


async def notification_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()
    if not query.data.startswith("notif:"):
        return
    field = query.data.split(":", 1)[1]
    s = _services(context)
    settings = await s.repo.toggle_notification(query.from_user.id, field)
    await query.edit_message_reply_markup(reply_markup=notifications_keyboard(settings))


async def ustawienia(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = _services(context)
    user = update.effective_user
    if not user:
        return
    await s.repo.ensure_user(user.id, user.username, s.settings.default_unit)
    if len(context.args) >= 2 and context.args[0].lower() in {"jednostka", "unit"}:
        try:
            unit = float(context.args[1].replace(",", "."))
        except ValueError:
            await update.effective_message.reply_text("Jednostka musi być liczbą.")
            return
        if unit <= 0 or unit > 100000:
            await update.effective_message.reply_text("Podaj dodatnią jednostkę.")
            return
        await s.repo.set_user_unit(user.id, unit)
    unit = await s.repo.get_user_unit(user.id)
    await update.effective_message.reply_text(
        f"⚙️ USTAWIENIA\n\nJednostka: {unit:g}\n"
        "Statystyki procentowe są liczone niezależnie od wartości pieniężnej.\n\n"
        "Zmiana: /ustawienia jednostka 1.0\nPowiadomienia: /powiadomienia"
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = _services(context)
    now = datetime.now(s.settings.tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    api_ok = await s.api.health_check() if s.settings.football_api_configured else False
    fixtures = await s.repo.fixture_count_for_day(start, end)
    analyzed = await s.repo.analyzed_count_for_day(start, end)
    text = (
        "🤖 STATUS BOTA\n\n"
        "Telegram:\n🟢 działa\n\n"
        f"Football API:\n{'🟢 działa' if api_ok else '🔴 niedostępne'}\n\n"
        "Baza:\n🟢 działa\n\n"
        "Scheduler:\n🟢 działa\n\n"
        f"Dzisiejsze mecze:\n{fixtures}\n\n"
        f"Przeanalizowane:\n{analyzed}\n\n"
        f"Ostatnia aktualizacja:\n{now:%H:%M}"
    )
    if s.api.last_remaining_requests is not None:
        text += f"\n\nPozostały limit API (wg ostatniej odpowiedzi): {s.api.last_remaining_requests}"
    await update.effective_message.reply_text(text)


async def liga(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = _services(context)
    catalog = s.leagues.load()
    if not context.args:
        text = "🏆 OBSŁUGIWANE POPULARNE LIGI\n\n" + "\n".join(
            f"• {x.get('name')} ({x.get('country') or 'UEFA'})" for x in catalog
        )
        await _reply_chunks(update, text if catalog else "Lista lig zostanie zsynchronizowana po połączeniu z API.")
        return
    needle = " ".join(context.args).lower()
    matches = [x for x in catalog if needle in str(x.get("name", "")).lower() or needle in str(x.get("country", "")).lower()]
    await update.effective_message.reply_text(
        "\n".join(f"ID {x['id']}: {x['name']} ({x.get('country')})" for x in matches[:20]) or "Nie znaleziono takiej ligi w katalogu."
    )


async def druzyna(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.effective_message.reply_text("Podaj nazwę drużyny, np. /druzyna Arsenal")
        return
    s = _services(context)
    needle = " ".join(context.args).lower()
    fixtures = await s.api.fixtures_by_date(datetime.now(s.settings.tz).date(), s.settings.timezone)
    matches = [
        f for f in fixtures
        if needle in f["teams"]["home"]["name"].lower() or needle in f["teams"]["away"]["name"].lower()
    ]
    if not matches:
        await update.effective_message.reply_text("Nie znalazłem tej drużyny w dzisiejszych spotkaniach.")
        return
    lines = ["🔎 MECZE DRUŻYNY", ""]
    for f in matches:
        lines.append(f"ID {f['fixture']['id']}: {f['teams']['home']['name']} – {f['teams']['away']['name']}")
    await update.effective_message.reply_text("\n".join(lines))
