from dataclasses import dataclass
from itertools import combinations
from math import prod
from statistics import median
import re

MARKET_LABELS = {
    "goals": "⚽ Gole",
    "corners": "🚩 Rożne",
    "cards": "🟨 Kartki",
    "shots": "🥅 Strzały",
    "sot": "🎯 Celne",
    "fouls": "🛑 Faule",
    "offsides": "🚩 Spalone",
    "saves": "🧤 Bramkarz – obrony",
    "players": "👤 Zawodnicy",
    "1x2": "🏆 1X2 / Double Chance",
}
DEFAULT_MARKETS = {"goals", "corners", "cards", "sot"}

# Przybliżona korelacja kategorii; 0 = mała, 100 = bardzo wysoka.
CORRELATION = {
    frozenset({"goals", "1x2"}): 72,
    frozenset({"goals", "shots"}): 62,
    frozenset({"goals", "sot"}): 68,
    frozenset({"shots", "sot"}): 82,
    frozenset({"shots", "corners"}): 48,
    frozenset({"sot", "corners"}): 42,
    frozenset({"cards", "fouls"}): 76,
    frozenset({"cards", "goals"}): 18,
    frozenset({"corners", "cards"}): 15,
}


def _norm(text):
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _category_for_bet(name):
    n = _norm(name)
    if "corner" in n:
        return "corners"
    if "card" in n or "booking" in n:
        return "cards"
    if "shot on target" in n or "shots on target" in n:
        return "sot"
    if "shot" in n:
        return "shots"
    if "foul" in n:
        return "fouls"
    if "offside" in n:
        return "offsides"
    if "save" in n:
        return "saves"
    if "player" in n:
        return "players"
    if any(x in n for x in ("goals over", "goals under", "total goals", "both teams", "btts", "over/under")):
        return "goals"
    if any(x in n for x in ("match winner", "double chance", "1x2")):
        return "1x2"
    return None


def _candidate_label(bet_name, value):
    return f"{bet_name}: {value}"


def _extract_candidates_from_odds(odds_payload, allowed_markets, min_confidence, min_data_quality):
    if not odds_payload:
        return []
    grouped = {}
    for book in odds_payload.get("bookmakers") or []:
        for bet in book.get("bets") or []:
            category = _category_for_bet(bet.get("name"))
            if not category or category not in allowed_markets:
                continue
            for entry in bet.get("values") or []:
                try:
                    odd = float(entry.get("odd"))
                except (TypeError, ValueError):
                    continue
                # Nie budujemy buildera z dużych pojedynczych kursów.
                if not 1.08 <= odd <= 2.35:
                    continue
                value = str(entry.get("value") or "").strip()
                if not value:
                    continue
                key = (category, _norm(bet.get("name")), _norm(value))
                grouped.setdefault(key, []).append((odd, bet.get("name") or category, value))

    out = []
    for (category, _, _), rows in grouped.items():
        odds = [r[0] for r in rows]
        odd = round(median(odds), 2)
        # Przybliżenie rynkowe, NIE model. Używamy tylko do filtrowania jakości.
        market_probability = min(0.95, 1.0 / odd)
        confidence = round(market_probability * 100)
        data_quality = min(90, 55 + len(rows) * 7)
        if confidence < min_confidence or data_quality < min_data_quality:
            continue
        bet_name, value = rows[0][1], rows[0][2]
        out.append({
            "category": category,
            "market": bet_name,
            "selection": _candidate_label(bet_name, value),
            "value": value,
            "odds": odd,
            "probability": market_probability,
            "confidence": confidence,
            "data_quality": data_quality,
            "edge": 0.0,
            "analysis": (
                f"Rynek dostępny u {len(rows)} bukmacherów w danych API. "
                f"Mediana kursu {odd:.2f}; implikowane ok. {market_probability*100:.0f}%. "
                "Brak pełnego modelu historycznego = niższa jakość niż przy predykcji modelowej."
            ),
        })
    return out


def pair_correlation(a, b):
    if a["category"] == b["category"]:
        return 65
    return CORRELATION.get(frozenset({a["category"], b["category"]}), 25)


def combo_correlation(combo):
    if len(combo) < 2:
        return 0
    vals = [pair_correlation(a, b) for a, b in combinations(combo, 2)]
    return round(sum(vals) / len(vals))


@dataclass
class BuilderResult:
    status: str
    selections: list
    approximate_odds: float | None
    nearest: float | None
    correlation_score: int
    note: str


class BetBuilderOptimizer:
    def __init__(
        self,
        fixture_id,
        target_odds=3.0,
        allowed_markets=None,
        min_confidence=65,
        min_data_quality=60,
        max_selections=4,
    ):
        self.fixture_id = int(fixture_id)
        self.target_odds = float(target_odds)
        self.allowed_markets = set(allowed_markets or DEFAULT_MARKETS)
        self.min_confidence = int(min_confidence)
        self.min_data_quality = int(min_data_quality)
        self.max_selections = max(2, min(4, int(max_selections)))

    def optimize(self, odds_payload):
        candidates = _extract_candidates_from_odds(
            odds_payload,
            self.allowed_markets,
            self.min_confidence,
            self.min_data_quality,
        )
        if len(candidates) < 2:
            return BuilderResult(
                "none", [], None, None, 0,
                "Za mało rynków z realnym kursem i wystarczającą jakością danych."
            )

        preferred = (self.target_odds * 0.95, self.target_odds * 1.05)
        fallback = (self.target_odds * 0.90, self.target_odds * 1.10)
        combos = []
        for size in range(2, min(self.max_selections, len(candidates)) + 1):
            for combo in combinations(candidates, size):
                # unikaj dwóch prawie identycznych linii tego samego rynku
                selections = {x["selection"] for x in combo}
                if len(selections) != len(combo):
                    continue
                approx = prod(x["odds"] for x in combo)
                corr = combo_correlation(combo)
                avg_conf = sum(x["confidence"] for x in combo) / len(combo)
                avg_quality = sum(x["data_quality"] for x in combo) / len(combo)
                target_diff = abs(approx - self.target_odds) / self.target_odds
                # Target najważniejszy, potem korelacja, confidence i jakość.
                score = target_diff * 1000 + corr * 1.7 - avg_conf * 1.2 - avg_quality * 0.5
                combos.append((score, approx, corr, list(combo)))

        if not combos:
            return BuilderResult("none", [], None, None, 0, "Brak sensownych kombinacji.")

        pref = [x for x in combos if preferred[0] <= x[1] <= preferred[1]]
        if pref:
            best = min(pref, key=lambda x: x[0])
            return BuilderResult("preferred", best[3], best[1], best[1], best[2], "")

        fall = [x for x in combos if fallback[0] <= x[1] <= fallback[1]]
        if fall:
            best = min(fall, key=lambda x: x[0])
            return BuilderResult("fallback", best[3], best[1], best[1], best[2], "")

        nearest = min(combos, key=lambda x: abs(x[1] - self.target_odds))
        return BuilderResult(
            "outside", [], None, nearest[1], nearest[2],
            "Najbliższa kombinacja jest poza dopuszczalną tolerancją ±10%."
        )
