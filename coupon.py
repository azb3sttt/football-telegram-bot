from itertools import combinations
from math import prod
from statistics import median

POPULAR_LEAGUES = [
    (39, "Premier League", "🏴"),
    (140, "La Liga", "🇪🇸"),
    (135, "Serie A", "🇮🇹"),
    (78, "Bundesliga", "🇩🇪"),
    (61, "Ligue 1", "🇫🇷"),
    (2, "Liga Mistrzów", "⭐"),
    (3, "Liga Europy", "🟠"),
    (848, "Liga Konferencji", "🟢"),
    (106, "Ekstraklasa", "🇵🇱"),
    (40, "Championship", "🏴"),
    (88, "Eredivisie", "🇳🇱"),
    (94, "Primeira Liga", "🇵🇹"),
    (144, "Jupiler Pro League", "🇧🇪"),
    (203, "Süper Lig", "🇹🇷"),
    (179, "Scottish Premiership", "🏴"),
    (253, "MLS", "🇺🇸"),
    (71, "Brasileirão Série A", "🇧🇷"),
    (128, "Liga Profesional Argentina", "🇦🇷"),
    (307, "Saudi Pro League", "🇸🇦"),
    (218, "Austrian Bundesliga", "🇦🇹"),
]
LEAGUE_BY_NUMBER = {i + 1: x for i, x in enumerate(POPULAR_LEAGUES)}
POPULAR_IDS = {x[0] for x in POPULAR_LEAGUES}

CONFIDENCE_PROFILES = {
    "safe": 75,
    "normal": 65,
    "risky": 55,
}
DEFAULT_PROFILE = "normal"

MARKET_ALIASES = {
    "gole": "goals", "goals": "goals",
    "rogi": "corners", "corners": "corners",
    "kartki": "cards", "cards": "cards",
    "celne": "sot", "sot": "sot",
    "strzaly": "shots", "strzały": "shots", "shots": "shots",
    "faule": "fouls", "fouls": "fouls",
    "1x2": "1x2", "wynik": "1x2",
    "mix": "mix", "single": "single", "builder": "builder",
}


def league_menu(fixtures=None, coupon_mode=False):
    today_ids = None
    if fixtures is not None:
        today_ids = {int((f.get("league") or {}).get("id") or 0) for f in fixtures}
    lines = (
        ["🏆 WYBIERZ LIGI DO KUPONU", "", "Numery np. 1,2 • 0 = wszystkie dostępne", ""]
        if coupon_mode
        else ["⚽ WYBIERZ LIGĘ", "", "Wpisz np. /dzisiaj 1", ""]
    )
    for number, (league_id, name, icon) in LEAGUE_BY_NUMBER.items():
        mark = ""
        if today_ids is not None:
            mark = " ✅" if league_id in today_ids else " —"
        lines.append(f"{number}. {icon} {name}{mark}")
    if coupon_mode:
        lines += ["", "0. 🌍 Obojętnie — wszystkie dostępne ligi"]
    elif today_ids is not None:
        lines += ["", "✅ = są dziś mecze • — = dziś brak meczów"]
    return "\n".join(lines)


def parse_league_numbers(raw: str):
    text = (raw or "").strip().replace(" ", "")
    if text == "0":
        return None
    nums = []
    for part in text.split(","):
        if not part:
            continue
        n = int(part)
        if n not in LEAGUE_BY_NUMBER:
            raise ValueError(f"Nieprawidłowy numer ligi: {n}")
        if n not in nums:
            nums.append(n)
    if not nums:
        raise ValueError("Brak numeru ligi")
    return nums


def league_ids_from_numbers(numbers):
    return set(POPULAR_IDS) if numbers is None else {LEAGUE_BY_NUMBER[n][0] for n in numbers}


def league_names_from_numbers(numbers):
    return "Wszystkie dostępne ligi" if numbers is None else ", ".join(LEAGUE_BY_NUMBER[n][1] for n in numbers)


def parse_market_filter(raw: str | None):
    if not raw:
        return {"1x2"}
    parts = {x.strip().lower() for x in raw.split(",") if x.strip()}
    out = set()
    for p in parts:
        mapped = MARKET_ALIASES.get(p)
        if mapped:
            out.add(mapped)
    return out or {"1x2"}


def parse_probability(value):
    if value is None:
        return None
    try:
        return float(str(value).replace("%", "").strip()) / 100.0
    except (TypeError, ValueError):
        return None


def _match_winner_odds(odds_payload):
    if not odds_payload:
        return {}
    values = {"Home": [], "Draw": [], "Away": []}
    for bookmaker in odds_payload.get("bookmakers") or []:
        for bet in bookmaker.get("bets") or []:
            name = (bet.get("name") or "").strip().lower()
            if name not in {"match winner", "1x2", "winner"}:
                continue
            for entry in bet.get("values") or []:
                raw = str(entry.get("value") or "").strip().lower()
                label = "Home" if raw in {"home", "1"} else "Draw" if raw in {"draw", "x"} else "Away" if raw in {"away", "2"} else None
                if not label:
                    continue
                try:
                    odd = float(entry.get("odd"))
                except (TypeError, ValueError):
                    continue
                if 1.01 <= odd <= 10:
                    values[label].append(odd)
    return {k: round(median(v), 2) for k, v in values.items() if v}


def candidate_from_prediction(
    fixture,
    prediction_payload,
    odds_payload,
    profile="normal",
    max_selection_odds=None,
):
    """Wysokiej jakości kandydat 1X2. Rynek bez modelu nie trafia automatycznie do kuponu."""
    min_conf = CONFIDENCE_PROFILES.get(profile, 65)
    if max_selection_odds is None:
        max_selection_odds = {"safe": 2.10, "normal": 2.50, "risky": 3.00}.get(profile, 2.50)

    odds_map = _match_winner_odds(odds_payload)
    if not odds_map:
        return None

    predictions = (prediction_payload or {}).get("predictions") or {}
    percent = predictions.get("percent") or {}
    model_probs = {
        "Home": parse_probability(percent.get("home")),
        "Draw": parse_probability(percent.get("draw")),
        "Away": parse_probability(percent.get("away")),
    }
    model_probs = {k: v for k, v in model_probs.items() if v is not None and k in odds_map}
    if not model_probs:
        return None

    # Nie wybieramy "największego edge" w ciemno. Najpierw minimalny confidence i rozsądny kurs.
    ranked = sorted(model_probs.items(), key=lambda kv: kv[1], reverse=True)
    chosen = None
    for label, probability in ranked:
        odd = odds_map[label]
        confidence = round(probability * 100)
        if confidence < min_conf:
            continue
        if odd > max_selection_odds:
            continue
        # Remis wymaga jeszcze większej pewności.
        if label == "Draw" and confidence < max(75, min_conf + 5):
            continue
        implied = 1.0 / odd
        edge = probability - implied
        if edge < -0.06:
            continue
        chosen = (label, probability, odd, confidence, edge)
        break

    if not chosen:
        return None

    label, probability, odd, confidence, edge = chosen
    teams = fixture.get("teams") or {}
    home = (teams.get("home") or {}).get("name", "?")
    away = (teams.get("away") or {}).get("name", "?")
    raw = {"Home": "1", "Draw": "X", "Away": "2"}[label]
    selection = home + " wygra (1)" if label == "Home" else away + " wygra (2)" if label == "Away" else "Remis (X)"
    league = (fixture.get("league") or {}).get("name", "")
    advice = predictions.get("advice") or ""
    reason = (
        f"Model API-Football: {probability*100:.0f}%. "
        f"Implikowane z kursu {odd:.2f}: {(1/odd)*100:.1f}%. "
        f"Confidence {confidence}/100."
    )
    if advice:
        reason += f" Wskazówka API: {advice}."

    return {
        "fixture_id": int((fixture.get("fixture") or {}).get("id")),
        "league": league,
        "home": home,
        "away": away,
        "market": "1X2",
        "selection": selection,
        "raw_selection": raw,
        "line": None,
        "odds": float(odd),
        "probability": float(probability),
        "confidence": confidence,
        "edge": float(edge),
        "data_quality": 85,
        "reason": reason,
        "source": "model+kurs",
    }


def _combo_score(combo, target):
    combined = prod(float(x["odds"]) for x in combo)
    target_diff = abs(combined - target) / target
    avg_conf = sum(float(x.get("confidence", 0)) for x in combo) / len(combo)
    avg_edge = sum(float(x.get("edge", 0)) for x in combo) / len(combo)
    avg_quality = sum(float(x.get("data_quality", 70)) for x in combo) / len(combo)
    # target ma największą wagę; jakość dopiero rozstrzyga w ramach tolerancji.
    score = (
        target_diff * 1000
        - avg_conf * 1.8
        - avg_quality * 0.7
        - max(-0.10, min(0.15, avg_edge)) * 100
    )
    return score, combined


def optimize_coupon(candidates, target_odds, event_count=None):
    """
    HARD TARGET:
      preferowany: target ±5%
      awaryjny:    target ±10%
    Poza ±10% kupon NIE jest zwracany.
    """
    target = float(target_odds)
    preferred = (target * 0.95, target * 1.05)
    fallback = (target * 0.90, target * 1.10)

    # Jedna selekcja na mecz.
    best_by_fixture = {}
    for c in candidates:
        fid = int(c["fixture_id"])
        old = best_by_fixture.get(fid)
        if old is None or (c["confidence"], c["data_quality"], c["edge"]) > (old["confidence"], old["data_quality"], old["edge"]):
            best_by_fixture[fid] = c
    pool = sorted(best_by_fixture.values(), key=lambda c: (c["confidence"], c["data_quality"], c["edge"]), reverse=True)[:18]

    sizes = [int(event_count)] if event_count else list(range(2, min(5, len(pool)) + 1))
    all_scored = []
    for size in sizes:
        if size < 1 or size > len(pool):
            continue
        for combo in combinations(pool, size):
            score, combined = _combo_score(combo, target)
            all_scored.append((score, combined, list(combo)))

    if not all_scored:
        return {"status": "none", "picks": [], "combined": None, "nearest": None}

    preferred_matches = [x for x in all_scored if preferred[0] <= x[1] <= preferred[1]]
    if preferred_matches:
        best = min(preferred_matches, key=lambda x: x[0])
        return {"status": "preferred", "picks": best[2], "combined": best[1], "nearest": best[1]}

    fallback_matches = [x for x in all_scored if fallback[0] <= x[1] <= fallback[1]]
    if fallback_matches:
        best = min(fallback_matches, key=lambda x: x[0])
        return {"status": "fallback", "picks": best[2], "combined": best[1], "nearest": best[1]}

    nearest = min(all_scored, key=lambda x: abs(x[1] - target))
    return {"status": "outside", "picks": [], "combined": None, "nearest": nearest[1]}


def choose_coupon(candidates, target_odds, event_count=3):
    """Kompatybilność ze starszym kodem; zwraca tylko kupon w tolerancji ±10%."""
    result = optimize_coupon(candidates, target_odds, event_count)
    return result["picks"]
