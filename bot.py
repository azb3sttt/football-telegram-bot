import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config import settings
from reports import format_report, format_coupon_report, by_market, summarize
from coupon import (
    LEAGUE_BY_NUMBER, POPULAR_IDS, league_menu, parse_league_numbers,
    league_ids_from_numbers, league_names_from_numbers,
    candidate_from_prediction, optimize_coupon, CONFIDENCE_PROFILES,
    DEFAULT_PROFILE, parse_market_filter,
)
from betbuilder import BetBuilderOptimizer, MARKET_LABELS, DEFAULT_MARKETS


PL = ZoneInfo(settings.timezone)


async def _ensure(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = context.application.bot_data["db"]
    user = update.effective_user
    if user:
        await db.ensure_user(user.id, user.username)
    return db


def _fixture_time(item):
    dt = (item.get("fixture") or {}).get("date", "")
    try:
        return datetime.fromisoformat(dt.replace("Z", "+00:00")).astimezone(PL)
    except Exception:
        return datetime.max.replace(tzinfo=PL)


def _format_fixture(item):
    fixture = item.get("fixture") or {}
    teams = item.get("teams") or {}
    status = (fixture.get("status") or {}).get("short", "")
    home = (teams.get("home") or {}).get("name", "?")
    away = (teams.get("away") or {}).get("name", "?")
    try:
        hhmm = _fixture_time(item).strftime("%H:%M")
    except Exception:
        hhmm = "--:--"
    return f"{hhmm} • {home} – {away} [{status}]"


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _ensure(update, context)
    await update.message.reply_text(
        "⚽ Football Bot działa.\n\n"
        "Najważniejsze komendy:\n"
        "/dzisiaj – wybór ligi i dzisiejsze mecze\n"
        "/dzisiaj 1 – mecze z ligi nr 1\n"
        "/ligi – lista 20 głównych lig\n"
        "/kupon – kreator z twardym celem kursu\n"
        "/kupon 3 single – zwykły kupon pod ~3.00\n"
        "/kupon 3 mecze=2 – dokładnie 2 mecze\n"
        "/profil safe|normal|risky – próg confidence\n"
        "/betbuilder – Bet Builder z jednego meczu\n"
        "/kupon 5 1,2 3 – skrót: kurs 5, ligi 1+2, 3 zdarzenia\n"
        "/anuluj – przerwij kreator kuponu\n"
        "/wyniki – ostatnie rozliczone typy\n"
        "/tydzien – raport 7 dni\n"
        "/miesiac – raport miesiąca\n"
        "/yield – yield według rynku\n"
        "/historia – historia typów\n"
        "/powiadomienia – ustawienia\n"
        "/status – stan bota i limit API\n\n"
        "Kupony są analizą statystyczną, nie gwarancją wyniku. Bot nie stosuje progresji stawek."
    )


async def cmd_pomoc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


async def cmd_ligi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _ensure(update, context)
    api = context.application.bot_data["api"]
    today = datetime.now(PL).date().isoformat()
    try:
        fixtures = await api.fixtures_by_date(today)
    except Exception:
        fixtures = []
    await update.message.reply_text(league_menu(fixtures))


async def cmd_dzisiaj(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _ensure(update, context)
    api = context.application.bot_data["api"]
    today = datetime.now(PL).date().isoformat()
    try:
        fixtures = await api.fixtures_by_date(today)
    except Exception:
        await update.message.reply_text(
            "Nie udało się pobrać danych. Spróbuję ponownie przy następnym odświeżeniu."
        )
        return

    if not fixtures:
        await update.message.reply_text("Nie znaleziono dzisiejszych spotkań.")
        return

    if not context.args:
        await update.message.reply_text(league_menu(fixtures))
        return

    try:
        number = int(context.args[0])
        league_id, league_name, icon = LEAGUE_BY_NUMBER[number]
    except (ValueError, KeyError):
        await update.message.reply_text("Podaj numer ligi od 1 do 20, np. /dzisiaj 1")
        return

    selected = [
        f for f in fixtures
        if int((f.get("league") or {}).get("id") or 0) == league_id
    ]
    if not selected:
        await update.message.reply_text(
            f"{icon} {league_name}\n\nDzisiaj nie ma spotkań tej ligi w danych API."
        )
        return

    selected.sort(key=_fixture_time)
    lines = [f"{icon} {league_name} — DZISIAJ", ""]
    for item in selected:
        lines.append(_format_fixture(item))
    await update.message.reply_text("\n".join(lines))


def _clear_coupon_wizard(context):
    for key in list(context.user_data.keys()):
        if key.startswith("coupon_"):
            context.user_data.pop(key, None)


def _coupon_profile(context):
    return context.user_data.get("coupon_profile", DEFAULT_PROFILE)


async def _ask_coupon_leagues(update, context):
    api = context.application.bot_data["api"]
    today = datetime.now(PL).date().isoformat()
    try:
        fixtures = await api.fixtures_by_date(today)
    except Exception:
        fixtures = []
    context.user_data["coupon_step"] = "leagues"
    await update.message.reply_text(league_menu(fixtures, coupon_mode=True))


async def _ask_coupon_count(update, context):
    context.user_data["coupon_step"] = "count"
    leagues_text = league_names_from_numbers(context.user_data.get("coupon_leagues"))
    await update.message.reply_text(
        "🔢 ILE MECZÓW/ZDARZEŃ?\n\n"
        f"Ligi: {leagues_text}\n"
        "Wpisz 1–5 albo AUTO.\n\n"
        "AUTO pozwala optimizerowi dobrać 2–5 zdarzeń tak, żeby kurs był jak najbliżej celu."
    )


async def _build_coupon(update, context, target, league_numbers=None, event_count=None, profile="normal", mode="single", markets=None):
    db = await _ensure(update, context)
    api = context.application.bot_data["api"]

    target = float(target)
    if not 1.50 <= target <= 25.0:
        await update.message.reply_text("Kurs docelowy musi być od 1.50 do 25.00.")
        return

    if mode in {"builder", "mix"}:
        await update.message.reply_text(
            "🧩 Tryb builder/mix używa Bet Buildera z kursem orientacyjnym, jeśli API nie ma oficjalnego kursu SGP.\n"
            "Najpewniejsze budowanie samego Bet Buildera: /betbuilder"
        )

    selected_ids = league_ids_from_numbers(league_numbers)
    selected_names = league_names_from_numbers(league_numbers)
    min_conf = CONFIDENCE_PROFILES.get(profile, 65)

    dates = [
        datetime.now(PL).date().isoformat(),
        (datetime.now(PL).date() + timedelta(days=1)).isoformat(),
    ]
    fixtures = []
    for d in dates:
        try:
            fixtures.extend(await api.fixtures_by_date(d))
        except Exception:
            pass

    now = datetime.now(PL)
    available = []
    seen = set()
    for f in fixtures:
        fid = int((f.get("fixture") or {}).get("id") or 0)
        league_id = int((f.get("league") or {}).get("id") or 0)
        status = (((f.get("fixture") or {}).get("status") or {}).get("short") or "").upper()
        if not fid or fid in seen or league_id not in selected_ids or status not in {"NS", "TBD"}:
            continue
        if _fixture_time(f) < now - timedelta(minutes=10):
            continue
        seen.add(fid)
        available.append(f)
    available.sort(key=_fixture_time)

    required = int(event_count) if event_count else 2
    if len(available) < required:
        await update.message.reply_text(
            f"Za mało nadchodzących meczów: potrzebuję co najmniej {required}, mam {len(available)}.\n"
            "Wybierz więcej lig albo 0 = wszystkie."
        )
        return

    # Limit requestów na free planie.
    analysis_limit = min(len(available), max(8, (event_count or 3) * 3), 12)
    available = available[:analysis_limit]
    msg = await update.message.reply_text(
        "🔎 OPTYMALIZUJĘ KUPON\n\n"
        f"🎯 Cel: {target:.2f}\n"
        f"🏆 Ligi: {selected_names}\n"
        f"⭐ Profil: {profile.upper()} (min {min_conf}/100)\n"
        f"📋 Tryb: {mode}\n"
        f"🔢 Mecze: {event_count if event_count else 'AUTO'}\n\n"
        f"Preferowany kurs: {target*0.95:.2f}–{target*1.05:.2f}\n"
        f"Awaryjny maksimum: {target*0.90:.2f}–{target*1.10:.2f}"
    )

    candidates = []
    for fixture in available:
        fid = int((fixture.get("fixture") or {}).get("id"))
        try:
            pred, odds = await api.prediction_and_odds(fid)
        except Exception:
            continue
        c = candidate_from_prediction(fixture, pred, odds, profile=profile)
        if c:
            candidates.append(c)

    result = optimize_coupon(candidates, target, event_count)
    if result["status"] in {"none", "outside"}:
        nearest = result.get("nearest")
        lines = [
            f"❌ Nie znalazłem wystarczająco dobrego kuponu w pobliżu kursu {target:.2f}.",
            "",
            f"Profil: {profile.upper()} • min confidence {min_conf}/100",
            f"Znalezionych jakościowych typów: {len(candidates)}",
        ]
        if nearest:
            lines += ["", f"Najbliższy sensowny: {nearest:.2f}"]
            if target*0.90 <= nearest <= target*1.10:
                lines.append("Mieści się w awaryjnej tolerancji, ale nie został wybrany automatycznie.")
            else:
                lines.append("Jest poza dopuszczalnym zakresem ±10%, więc NIE zwracam go jako kupon.")
        lines += [
            "",
            "Możesz:",
            "• zmienić liczbę meczów na AUTO,",
            "• wybrać więcej lig,",
            "• użyć /betbuilder,",
            "• przełączyć profil: /profil risky.",
        ]
        await msg.edit_text("\n".join(lines))
        _clear_coupon_wizard(context)
        return

    picks = result["picks"]
    coupon_id, combined = await db.create_coupon(update.effective_user.id, picks)
    band = "✅ preferowany zakres ±5%" if result["status"] == "preferred" else "⚠️ awaryjny zakres ±10%"
    lines = [
        "🎟 KUPON STATYSTYCZNY",
        f"ID: {coupon_id}",
        f"🎯 Cel: {target:.2f}",
        f"💰 Kurs: {combined:.2f}",
        band,
        f"🏆 Ligi: {selected_names}",
        f"⭐ Profil: {profile.upper()}",
        "",
    ]
    for i, p in enumerate(picks, 1):
        lines += [
            f"{i}️⃣ {p['home']} – {p['away']}",
            f"🎯 {p['selection']}",
            f"Kurs: {p['odds']:.2f}",
            f"Confidence: {p['confidence']}/100",
            f"Edge: {p['edge']*100:+.1f} pp",
            f"Dane: {p['data_quality']}/100",
            "",
        ]
    lines += [
        "💡 Propozycja nie liczy się do yield.",
        f"Jeśli grasz: /stawiam {coupon_id}",
        f"Ze stawką 2.5u: /stawiam {coupon_id} 2.5",
    ]
    await msg.edit_text("\n".join(lines))
    _clear_coupon_wizard(context)


async def cmd_profil(update, context):
    await _ensure(update, context)
    if not context.args:
        current = context.user_data.get("risk_profile", DEFAULT_PROFILE)
        await update.message.reply_text(
            "⭐ PROFILE JAKOŚCI\n\n"
            "SAFE: min 75/100, niższe pojedyncze kursy\n"
            "NORMAL: min 65/100 (domyślny)\n"
            "RISKY: min 55/100\n\n"
            f"Aktualny: {current.upper()}\n"
            "Zmiana: /profil safe | /profil normal | /profil risky"
        )
        return
    p = context.args[0].lower()
    if p not in CONFIDENCE_PROFILES:
        await update.message.reply_text("Dostępne: safe, normal, risky.")
        return
    context.user_data["risk_profile"] = p
    await update.message.reply_text(f"✅ Profil ustawiony na {p.upper()} (min {CONFIDENCE_PROFILES[p]}/100).")


async def cmd_kupon(update, context):
    await _ensure(update, context)
    _clear_coupon_wizard(context)
    profile = context.user_data.get("risk_profile", DEFAULT_PROFILE)

    # Obsługa wygodnych skrótów:
    # /kupon 3
    # /kupon 3 single
    # /kupon 3 rogi,kartki
    # /kupon 3 mecze=2
    # /kupon 5 1,2 3  (stary skrót: ligi + liczba)
    if context.args:
        try:
            target = float(context.args[0].replace(",", "."))
        except ValueError:
            await update.message.reply_text("Pierwszy parametr musi być kursem, np. /kupon 3")
            return
        league_numbers = None
        event_count = None
        mode = "single"
        markets = {"1x2"}

        for arg in context.args[1:]:
            low = arg.lower()
            if low in {"single", "builder", "mix"}:
                mode = low
            elif low.startswith("mecze=") or low.startswith("zdarzenia="):
                try:
                    event_count = int(low.split("=", 1)[1])
                except ValueError:
                    pass
            elif re.fullmatch(r"\d+(,\d+)*", low):
                try:
                    league_numbers = parse_league_numbers(low)
                except Exception:
                    pass
            elif "," in low or low in {"gole","rogi","kartki","celne","strzaly","strzały","faule","1x2"}:
                markets = parse_market_filter(low)

        # Stara składnia /kupon 5 1,2 3
        if len(context.args) >= 3 and re.fullmatch(r"\d+(,\d+)*", context.args[1]):
            try:
                event_count = int(context.args[2])
            except Exception:
                pass

        if event_count is not None and not 1 <= event_count <= 5:
            await update.message.reply_text("mecze=/zdarzenia= musi być od 1 do 5.")
            return
        await _build_coupon(update, context, target, league_numbers, event_count, profile, mode, markets)
        return

    context.user_data["coupon_profile"] = profile
    context.user_data["coupon_step"] = "odds"
    await update.message.reply_text(
        "🎟 KREATOR KUPONU — KROK 1/3\n\n"
        "Jaki kurs docelowy?\n"
        "Np. 3 albo 5.\n\n"
        "Bot zaakceptuje automatycznie tylko:\n"
        "✅ ±5% preferowane\n"
        "⚠️ maksymalnie ±10% awaryjnie\n\n"
        "Przerwij: /anuluj"
    )


async def cmd_anuluj(update, context):
    had = bool(context.user_data.get("coupon_step") or context.user_data.get("bb_fixture_id"))
    _clear_coupon_wizard(context)
    for key in list(context.user_data.keys()):
        if key.startswith("bb_"):
            context.user_data.pop(key, None)
    await update.message.reply_text("❎ Anulowano." if had else "Nie masz aktywnego kreatora.")


async def coupon_target_message(update, context):
    # Custom target dla Bet Buildera.
    if context.user_data.get("bb_step") == "custom_target":
        try:
            value = float((update.message.text or "").replace(",", "."))
        except ValueError:
            await update.message.reply_text("Podaj kurs, np. 3 albo 4.5.")
            return
        if not 1.5 <= value <= 15:
            await update.message.reply_text("Dla Bet Buildera wybierz kurs 1.50–15.00.")
            return
        context.user_data["bb_target"] = value
        context.user_data["bb_step"] = None
        await update.message.reply_text(f"✅ Cel buildera: {value:.2f}. Kliknij 🔍 ANALIZUJ.")
        return

    step = context.user_data.get("coupon_step")
    if not step:
        return
    raw = (update.message.text or "").strip()

    if step == "odds":
        try:
            target = float(raw.replace(",", "."))
        except ValueError:
            await update.message.reply_text("Podaj kurs jako liczbę, np. 3.")
            return
        if not 1.5 <= target <= 25:
            await update.message.reply_text("Zakres 1.50–25.00.")
            return
        context.user_data["coupon_target"] = target
        await _ask_coupon_leagues(update, context)
        return

    if step == "leagues":
        try:
            leagues = parse_league_numbers(raw)
        except Exception:
            await update.message.reply_text("Wpisz np. 1,2 albo 0.")
            return
        context.user_data["coupon_leagues"] = leagues
        await _ask_coupon_count(update, context)
        return

    if step == "count":
        if raw.lower() == "auto":
            count = None
        else:
            try:
                count = int(raw)
            except ValueError:
                await update.message.reply_text("Wpisz 1–5 albo AUTO.")
                return
            if not 1 <= count <= 5:
                await update.message.reply_text("Wpisz 1–5 albo AUTO.")
                return
        await _build_coupon(
            update, context,
            context.user_data["coupon_target"],
            context.user_data.get("coupon_leagues"),
            count,
            context.user_data.get("coupon_profile", DEFAULT_PROFILE),
            "single",
            {"1x2"},
        )


def _builder_keyboard(context):
    selected = set(context.user_data.get("bb_markets") or DEFAULT_MARKETS)
    rows = []
    keys = ["goals","corners","cards","fouls","shots","sot","offsides","saves","players","1x2"]
    for i in range(0, len(keys), 2):
        row = []
        for key in keys[i:i+2]:
            mark = "✅" if key in selected else "⬜"
            row.append(InlineKeyboardButton(f"{mark} {MARKET_LABELS[key]}", callback_data=f"bb:toggle:{key}"))
        rows.append(row)
    target = float(context.user_data.get("bb_target", 3.0))
    count = int(context.user_data.get("bb_count", 3))
    rows += [
        [
            InlineKeyboardButton("2.00", callback_data="bb:target:2"),
            InlineKeyboardButton("3.00", callback_data="bb:target:3"),
            InlineKeyboardButton("5.00", callback_data="bb:target:5"),
            InlineKeyboardButton("✏️ Inny", callback_data="bb:target:custom"),
        ],
        [
            InlineKeyboardButton("2 zd.", callback_data="bb:count:2"),
            InlineKeyboardButton("3 zd.", callback_data="bb:count:3"),
            InlineKeyboardButton("4 zd.", callback_data="bb:count:4"),
        ],
        [InlineKeyboardButton(f"🔍 ANALIZUJ • cel {target:.2f} • {count} zd.", callback_data="bb:build")],
    ]
    return InlineKeyboardMarkup(rows)


async def _show_builder_menu(update, context, fixture):
    fid = int((fixture.get("fixture") or {}).get("id"))
    teams = fixture.get("teams") or {}
    home = (teams.get("home") or {}).get("name", "?")
    away = (teams.get("away") or {}).get("name", "?")
    context.user_data["bb_fixture_id"] = fid
    context.user_data.setdefault("bb_markets", set(DEFAULT_MARKETS))
    context.user_data.setdefault("bb_target", 3.0)
    context.user_data.setdefault("bb_count", 3)
    text = (
        "🧩 BET BUILDER\n"
        f"⚽ {home} – {away}\n\n"
        "Co mam analizować?\n"
        "Klikaj kategorie ON/OFF, wybierz kurs i liczbę zdarzeń."
    )
    if getattr(update, "callback_query", None):
        await update.callback_query.edit_message_text(text, reply_markup=_builder_keyboard(context))
    else:
        await update.message.reply_text(text, reply_markup=_builder_keyboard(context))


async def _find_builder_fixture(api, query_text):
    dates = [
        datetime.now(PL).date().isoformat(),
        (datetime.now(PL).date() + timedelta(days=1)).isoformat(),
    ]
    fixtures = []
    for d in dates:
        try:
            fixtures += await api.fixtures_by_date(d)
        except Exception:
            pass
    tokens = [x.lower() for x in re.findall(r"[A-Za-zÀ-ž0-9]+", query_text) if len(x) >= 3]
    best = None
    best_score = 0
    for f in fixtures:
        teams = f.get("teams") or {}
        text = f"{(teams.get('home') or {}).get('name','')} {(teams.get('away') or {}).get('name','')}".lower()
        score = sum(1 for t in tokens if t in text)
        if score > best_score:
            best_score = score
            best = f
    return best if best_score >= 1 else None


async def cmd_betbuilder(update, context):
    await _ensure(update, context)
    api = context.application.bot_data["api"]
    # /betbuilder Arsenal Chelsea 3.00
    if context.args:
        args = list(context.args)
        target = 3.0
        try:
            maybe = float(args[-1].replace(",", "."))
            target = maybe
            args = args[:-1]
        except Exception:
            pass
        fixture = await _find_builder_fixture(api, " ".join(args))
        if not fixture:
            await update.message.reply_text("Nie znalazłem tego meczu dziś/jutro. Użyj /betbuilder bez parametrów.")
            return
        context.user_data["bb_target"] = target
        await _show_builder_menu(update, context, fixture)
        return

    today = datetime.now(PL).date().isoformat()
    try:
        fixtures = await api.fixtures_by_date(today)
    except Exception:
        fixtures = []
    fixtures = [f for f in fixtures if (((f.get("fixture") or {}).get("status") or {}).get("short") or "").upper() in {"NS","TBD"}]
    if not fixtures:
        await update.message.reply_text("Nie znalazłem dzisiejszych nadchodzących meczów.")
        return
    buttons = []
    for f in fixtures[:16]:
        teams = f.get("teams") or {}
        home = (teams.get("home") or {}).get("name", "?")
        away = (teams.get("away") or {}).get("name", "?")
        fid = int((f.get("fixture") or {}).get("id"))
        buttons.append([InlineKeyboardButton(f"{home} – {away}", callback_data=f"bb:match:{fid}")])
    await update.message.reply_text("🧩 Wybierz mecz do Bet Buildera:", reply_markup=InlineKeyboardMarkup(buttons))


async def cmd_builder_market(update, context):
    # Aliasy typu /builder_gole — ustaw preset, a potem pokaż listę meczów.
    cmd = (update.message.text or "").split()[0].lower()
    preset = {
        "/builder_gole": {"goals"},
        "/builder_rogi": {"corners"},
        "/builder_kartki": {"cards"},
        "/builder_strzaly": {"shots"},
        "/builder_celne": {"sot"},
        "/builder_faule": {"fouls"},
    }.get(cmd, set(DEFAULT_MARKETS))
    context.user_data["bb_markets"] = preset
    await cmd_betbuilder(update, context)


async def builder_callback(update, context):
    q = update.callback_query
    await q.answer()
    data = q.data
    api = context.application.bot_data["api"]

    if data.startswith("bb:match:"):
        fid = int(data.rsplit(":", 1)[1])
        fixture = await api.fixture(fid)
        if not fixture:
            await q.edit_message_text("Nie udało się pobrać meczu.")
            return
        await _show_builder_menu(update, context, fixture)
        return

    if data.startswith("bb:toggle:"):
        key = data.rsplit(":", 1)[1]
        selected = set(context.user_data.get("bb_markets") or DEFAULT_MARKETS)
        if key in selected:
            selected.remove(key)
        else:
            selected.add(key)
        context.user_data["bb_markets"] = selected
        await q.edit_message_reply_markup(reply_markup=_builder_keyboard(context))
        return

    if data.startswith("bb:target:"):
        raw = data.rsplit(":", 1)[1]
        if raw == "custom":
            context.user_data["bb_step"] = "custom_target"
            await q.message.reply_text("✏️ Napisz własny kurs docelowy, np. 4.20.")
            return
        context.user_data["bb_target"] = float(raw)
        await q.edit_message_reply_markup(reply_markup=_builder_keyboard(context))
        return

    if data.startswith("bb:count:"):
        context.user_data["bb_count"] = int(data.rsplit(":", 1)[1])
        await q.edit_message_reply_markup(reply_markup=_builder_keyboard(context))
        return

    if data == "bb:build":
        fid = context.user_data.get("bb_fixture_id")
        if not fid:
            await q.message.reply_text("Najpierw wybierz mecz.")
            return
        fixture = await api.fixture(fid)
        odds = await api.odds(fid)
        markets = set(context.user_data.get("bb_markets") or DEFAULT_MARKETS)
        target = float(context.user_data.get("bb_target", 3.0))
        count = int(context.user_data.get("bb_count", 3))
        profile = context.user_data.get("risk_profile", DEFAULT_PROFILE)
        min_conf = CONFIDENCE_PROFILES.get(profile, 65)
        optimizer = BetBuilderOptimizer(
            fixture_id=fid,
            target_odds=target,
            allowed_markets=markets,
            min_confidence=min_conf,
            min_data_quality=60,
            max_selections=count,
        )
        result = optimizer.optimize(odds)
        teams = fixture.get("teams") or {}
        home = (teams.get("home") or {}).get("name", "?")
        away = (teams.get("away") or {}).get("name", "?")

        if result.status in {"none","outside"}:
            lines = [
                "❌ Nie znalazłem Bet Buildera w wymaganym zakresie.",
                f"⚽ {home} – {away}",
                f"🎯 Cel: {target:.2f}",
            ]
            if result.nearest:
                lines += ["", f"Najbliższy orientacyjny iloczyn: {result.nearest:.2f}"]
            lines += ["", result.note, "Spróbuj włączyć więcej rynków albo zmienić cel."]
            await q.message.reply_text("\n".join(lines))
            return

        band = "✅ ±5%" if result.status == "preferred" else "⚠️ ±10%"
        lines = [
            "🧩 BET BUILDER",
            f"⚽ {home} – {away}",
            f"🎯 Cel: {target:.2f}",
            f"Zakres: {band}",
            "",
        ]
        for i, s in enumerate(result.selections, 1):
            lines += [
                f"{i}️⃣ {s['selection']}",
                f"Kategoria: {MARKET_LABELS.get(s['category'], s['category'])}",
                f"Kurs pojedynczy: {s['odds']:.2f}",
                f"Confidence rynkowy: {s['confidence']}/100",
                f"Data quality: {s['data_quality']}/100",
                f"Analiza: {s['analysis']}",
                "",
            ]
        lines += [
            f"🔗 Correlation score: {result.correlation_score}/100",
            f"📊 Orientacyjny iloczyn kursów: {result.approximate_odds:.2f}",
            "",
            "⚠️ To NIE jest gwarantowany kurs Bet Buildera.",
            "Bukmacher może wycenić połączone zdarzenia inaczej ze względu na korelację.",
        ]
        await q.message.reply_text("\n".join(lines))

async def cmd_wyniki(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = await _ensure(update, context)
    rows = await db.last_settled(update.effective_user.id, 10)
    if not rows:
        await update.message.reply_text("Brak zakończonych typów w historii.")
        return
    icons = {"won":"✅","lost":"❌","push":"↩️","void":"🚫"}
    lines = ["📋 OSTATNIE WYNIKI", ""]
    for r in rows:
        lines.append(
            f"{icons.get(r['status'],'•')} {r['home_team'] or ''} – {r['away_team'] or ''}\n"
            f"{r['selection']} @ {float(r['odds']):.2f}\n"
            f"{r['final_result'] or ''}\n"
        )
    await update.message.reply_text("\n".join(lines))


async def cmd_tydzien(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = await _ensure(update, context)
    since = (datetime.now(PL) - timedelta(days=7)).astimezone(ZoneInfo("UTC")).isoformat()
    rows = await db.report_rows(update.effective_user.id, since)
    await update.message.reply_text(format_report("RAPORT 7 DNI", rows))


async def cmd_miesiac(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = await _ensure(update, context)
    now = datetime.now(PL)
    since = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(ZoneInfo("UTC")).isoformat()
    rows = await db.report_rows(update.effective_user.id, since)
    await update.message.reply_text(format_report("RAPORT MIESIĄCA", rows))


async def cmd_yield(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = await _ensure(update, context)
    rows = await db.report_rows(update.effective_user.id)
    groups = by_market(rows)
    if not groups:
        await update.message.reply_text("Brak rozliczonych typów do wyliczenia yield.")
        return
    lines = ["📊 YIELD WEDŁUG RYNKU", ""]
    for market, s in sorted(groups.items()):
        lines.append(f"{market}: {s['count']} typów • Yield {s['yield']:+.2f}%")
    await update.message.reply_text("\n".join(lines))


async def cmd_historia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_wyniki(update, context)


def _notif_markup(row):
    def icon(v): return "✅" if v else "❌"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{icon(row['notify_coupon'])} Wynik kuponu", callback_data="notif:notify_coupon")],
        [InlineKeyboardButton(f"{icon(row['notify_selection'])} Typ zakończony", callback_data="notif:notify_selection")],
        [InlineKeyboardButton(f"{icon(row['notify_weekly'])} Raport tygodniowy", callback_data="notif:notify_weekly")],
        [InlineKeyboardButton(f"{icon(row['notify_monthly'])} Raport miesięczny", callback_data="notif:notify_monthly")],
    ])


async def cmd_powiadomienia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = await _ensure(update, context)
    row = await db.get_notification_settings(update.effective_user.id)
    await update.message.reply_text("🔔 POWIADOMIENIA", reply_markup=_notif_markup(row))


async def notifications_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    db = context.application.bot_data["db"]
    column = q.data.split(":", 1)[1]
    await db.toggle_notification(q.from_user.id, column)
    row = await db.get_notification_settings(q.from_user.id)
    await q.edit_message_reply_markup(reply_markup=_notif_markup(row))



def _coupon_status_icon(row):
    if int(row["is_placed"] or 0) == 0:
        return "💡"
    return {
        "pending": "⏳",
        "won": "✅",
        "lost": "❌",
        "push": "↩️",
        "void": "🚫",
    }.get(row["status"], "•")


async def cmd_stawiam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = await _ensure(update, context)
    if not context.args:
        await update.message.reply_text(
            "Użycie:\n/stawiam ID\n/stawiam ID STAWKA\n\n"
            "Przykład: /stawiam 23\nPrzykład: /stawiam 23 2.5"
        )
        return
    try:
        coupon_id = int(context.args[0])
        stake = float(context.args[1].replace(",", ".")) if len(context.args) > 1 else 1.0
    except ValueError:
        await update.message.reply_text("Podaj poprawne ID i stawkę, np. /stawiam 23 1.5")
        return
    if not (0.1 <= stake <= 1000):
        await update.message.reply_text("Stawka w trackerze musi być od 0.1 do 1000 units.")
        return
    ok, reason = await db.mark_coupon_placed(update.effective_user.id, coupon_id, stake)
    if not ok:
        msg = "Nie znaleziono takiego kuponu." if reason == "not_found" else "Ten kupon jest już zakończony."
        await update.message.reply_text(msg)
        return
    coupon, picks = await db.get_coupon(update.effective_user.id, coupon_id)
    action = "Zaktualizowano stawkę" if reason == "updated" else "Kupon oznaczony jako POSTAWIONY"
    await update.message.reply_text(
        f"✅ {action}\n\n"
        f"ID: {coupon_id}\n"
        f"Stawka: {stake:.2f} units\n"
        f"Kurs AKO: {float(coupon['combined_odds'] or 0):.2f}\n"
        f"Selekcje: {len(picks)}\n\n"
        "Od teraz ten kupon i jego selekcje liczą się do Twoich statystyk/yield."
    )


async def cmd_niegram(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = await _ensure(update, context)
    if not context.args:
        await update.message.reply_text("Użycie: /niegram ID, np. /niegram 23")
        return
    try:
        coupon_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID kuponu musi być liczbą.")
        return
    ok, reason = await db.unmark_coupon(update.effective_user.id, coupon_id)
    if not ok:
        msg = "Nie znaleziono takiego kuponu." if reason == "not_found" else "Nie można cofnąć zakończonego kuponu."
        await update.message.reply_text(msg)
        return
    await update.message.reply_text(
        f"💡 Kupon #{coupon_id} wrócił do propozycji i nie będzie liczony do Twojego yield."
    )


async def cmd_kuponinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = await _ensure(update, context)
    if not context.args:
        await update.message.reply_text("Użycie: /kuponinfo ID, np. /kuponinfo 23")
        return
    try:
        coupon_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID kuponu musi być liczbą.")
        return
    coupon, picks = await db.get_coupon(update.effective_user.id, coupon_id)
    if not coupon:
        await update.message.reply_text("Nie znaleziono takiego kuponu.")
        return
    lines = [
        f"🎟 KUPON #{coupon_id}",
        f"{'✅ POSTAWIONY' if coupon['is_placed'] else '💡 TYLKO PROPOZYCJA'}",
        f"Status: {coupon['status']}",
        f"Kurs AKO: {float(coupon['combined_odds'] or 0):.2f}",
    ]
    if coupon["is_placed"]:
        lines.append(f"Stawka: {float(coupon['stake_units'] or 1):.2f} units")
    lines.append("")
    icons = {"pending":"⏳","won":"✅","lost":"❌","push":"↩️","void":"🚫"}
    for i, p in enumerate(picks, 1):
        lines.append(
            f"{i}. {icons.get(p['status'],'•')} {p['home_team']} – {p['away_team']}\n"
            f"   {p['selection']} @ {float(p['odds']):.2f}"
        )
    if coupon["final_result"]:
        lines += ["", f"Wynik: {coupon['final_result']}"]
    if coupon["is_placed"] and coupon["profit_loss"] is not None:
        lines.append(f"Profit/Loss AKO: {float(coupon['profit_loss']):+.2f} units")
    await update.message.reply_text("\n".join(lines))


async def _list_coupon_command(update, context, placed=None, pending_only=False, title="KUPONY"):
    db = await _ensure(update, context)
    rows = await db.list_coupons(update.effective_user.id, placed=placed, pending_only=pending_only, limit=15)
    if not rows:
        await update.message.reply_text("Brak kuponów pasujących do tego filtra.")
        return
    lines = [f"🎟 {title}", ""]
    for c in rows:
        lines.append(
            f"{_coupon_status_icon(c)} #{c['coupon_id']} • @ {float(c['combined_odds'] or 0):.2f}"
            + (f" • {float(c['stake_units']):.2f}u" if c["is_placed"] else "")
            + f" • {c['status']}"
        )
    lines += ["", "Szczegóły: /kuponinfo ID"]
    await update.message.reply_text("\n".join(lines))


async def cmd_mojekupony(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _list_coupon_command(update, context, placed=1, title="MOJE POSTAWIONE KUPONY")


async def cmd_aktywny(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _list_coupon_command(update, context, placed=1, pending_only=True, title="AKTYWNE KUPONY")


async def cmd_proponowane(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _list_coupon_command(update, context, placed=0, pending_only=True, title="PROPOZYCJE BOTA")


async def cmd_bilans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = await _ensure(update, context)
    coupon_rows = await db.coupon_report_rows(update.effective_user.id)
    selection_rows = await db.report_rows(update.effective_user.id)
    if not coupon_rows and not selection_rows:
        await update.message.reply_text("Nie masz jeszcze rozliczonych postawionych kuponów.")
        return
    text = format_coupon_report("BILANS POSTAWIONYCH KUPONÓW", coupon_rows)
    if selection_rows:
        s = summarize(selection_rows)
        text += (
            "\n\n📌 SELEKCJE (modelowo 1u każda)\n"
            f"Typy: {s['count']}\n"
            f"Profit: {s['profit']:+.2f}u\n"
            f"Yield: {s['yield_']:+.2f}%"
        )
    await update.message.reply_text(text)


async def cmd_statystyki(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = await _ensure(update, context)
    rows = await db.report_rows(update.effective_user.id)
    if not rows:
        await update.message.reply_text("Brak rozliczonych postawionych selekcji.")
        return
    s = summarize(rows)
    groups = by_market(rows)
    best = max(groups.items(), key=lambda kv: kv[1]["yield_"]) if groups else None
    worst = min(groups.items(), key=lambda kv: kv[1]["yield_"]) if groups else None
    text = (
        "📊 STATYSTYKI BOTA — TYLKO POSTAWIONE\n\n"
        f"Selekcje: {s['count']}\n"
        f"Trafione: {s['wins']}\n"
        f"Nietrafione: {s['losses']}\n"
        f"Skuteczność: {s['hit_rate']:.2f}%\n"
        f"Profit: {s['profit']:+.2f}u\n"
        f"Yield: {s['yield_']:+.2f}%\n"
        f"Średni kurs: {s['avg_odds']:.2f}\n"
        f"Średni confidence: {s['avg_conf']:.0f}/100"
    )
    if best:
        text += f"\n\n🏆 Najlepszy rynek: {best[0]} ({best[1]['yield_']:+.2f}%)"
    if worst:
        text += f"\n📉 Najsłabszy rynek: {worst[0]} ({worst[1]['yield_']:+.2f}%)"
    await update.message.reply_text(text)


async def cmd_seria(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = await _ensure(update, context)
    rows = await db.last_settled(update.effective_user.id, 50, placed_only=True)
    decided = [r for r in rows if r["status"] in ("won", "lost")]
    if not decided:
        await update.message.reply_text("Brak wystarczających rozliczonych typów.")
        return
    first = decided[0]["status"]
    streak = 0
    for r in decided:
        if r["status"] == first:
            streak += 1
        else:
            break
    icon = "🔥" if first == "won" else "🧊"
    word = "trafień" if first == "won" else "pudeł"
    await update.message.reply_text(f"{icon} Aktualna seria: {streak} {word} z rzędu.")


async def cmd_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _ensure(update, context)
    api = context.application.bot_data["api"]
    try:
        await api.ping()
    except Exception:
        pass
    remaining = getattr(api, "daily_remaining", None)
    used = getattr(api, "daily_used", None)
    await update.message.reply_text(
        "📡 LIMIT FOOTBALL API\n\n"
        f"Pozostało dzisiaj: {remaining if remaining is not None else '?'}\n"
        f"Zużyto: {used if used is not None else '?'}"
    )

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = await _ensure(update, context)
    api = context.application.bot_data["api"]
    today = datetime.now(PL).date().isoformat()
    api_ok = True
    fixtures_count = 0
    try:
        fixtures_count = len(await api.fixtures_by_date(today))
    except Exception:
        api_ok = False
    analyzed = await db.count_today_analyzed()
    quota = f"{api.daily_remaining}/{api.daily_limit}" if api.daily_remaining is not None else "brak danych"
    await update.message.reply_text(
        "🤖 STATUS BOTA\n\n"
        "Telegram:\n🟢 działa\n\n"
        f"Football API:\n{'🟢 działa' if api_ok else '🔴 niedostępne'}\n\n"
        f"Limit API pozostały:\n{quota}\n\n"
        "Baza:\n🟢 działa\n\n"
        "Scheduler:\n🟢 działa\n\n"
        f"Dzisiejsze mecze:\n{fixtures_count}\n\n"
        f"Przeanalizowane/zapisane typy dziś:\n{analyzed}\n\n"
        f"Ostatnia aktualizacja:\n{datetime.now(PL).strftime('%H:%M')}"
    )
