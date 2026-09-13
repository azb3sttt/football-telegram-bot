from dataclasses import dataclass


@dataclass
class Settlement:
    status: str
    final_result: str
    profit_loss: float


def _profit(status: str, odds: float) -> float:
    if status == "won":
        return round(odds - 1.0, 4)
    if status == "lost":
        return -1.0
    return 0.0


def _stat_map(stats_response):
    out = {}
    for team_block in stats_response or []:
        team_name = (team_block.get("team") or {}).get("name", "")
        vals = {}
        for s in team_block.get("statistics") or []:
            vals[s.get("type")] = s.get("value")
        out[team_name] = vals
    return out


def settle(selection_row, fixture, stats_response) -> Settlement:
    odds = float(selection_row["odds"])
    market = (selection_row["market"] or "").lower()
    selection = (selection_row["selection"] or "").lower()
    line = selection_row["line"]
    line = float(line) if line is not None else None

    fixture_data = fixture.get("fixture") or {}
    short = ((fixture_data.get("status") or {}).get("short") or "").upper()
    if short in {"PST", "CANC", "ABD", "AWD", "WO"}:
        return Settlement("void", f"Status meczu: {short}", 0.0)
    if short not in {"FT", "AET", "PEN"}:
        return Settlement("pending", f"Status meczu: {short or 'oczekuje'}", 0.0)

    teams = fixture.get("teams") or {}
    goals = fixture.get("goals") or {}
    home_name = (teams.get("home") or {}).get("name", "")
    away_name = (teams.get("away") or {}).get("name", "")
    home_goals = int(goals.get("home") or 0)
    away_goals = int(goals.get("away") or 0)

    if market in {"1x2", "winner", "wynik"}:
        result = "draw"
        if home_goals > away_goals:
            result = "home"
        elif away_goals > home_goals:
            result = "away"
        wanted = "draw" if selection in {"x", "draw", "remis"} else (
            "home" if selection in {"1", "home", "gospodarze"} else "away"
        )
        status = "won" if result == wanted else "lost"
        return Settlement(status, f"{home_name} {home_goals}:{away_goals} {away_name}", _profit(status, odds))

    if market in {"btts", "obie strzela", "obie strzelą"}:
        yes = home_goals > 0 and away_goals > 0
        wants_yes = selection in {"yes", "tak", "btts yes", "btts tak"}
        status = "won" if yes == wants_yes else "lost"
        return Settlement(status, f"Gole: {home_goals}:{away_goals}", _profit(status, odds))

    if market in {"goals", "gole", "over_under", "over/under"}:
        total = home_goals + away_goals
        if line is None:
            return Settlement("void", "Brak linii", 0.0)
        if "over" in selection or "powyżej" in selection:
            status = "won" if total > line else ("push" if total == line else "lost")
        else:
            status = "won" if total < line else ("push" if total == line else "lost")
        return Settlement(status, f"Łącznie goli: {total}", _profit(status, odds))

    stats = _stat_map(stats_response)

    def team_stat(team, key):
        val = (stats.get(team) or {}).get(key)
        try:
            return int(val or 0)
        except Exception:
            return 0

    if market in {"corners", "rogi", "rzuty rożne"}:
        team = home_name if home_name.lower() in selection else away_name if away_name.lower() in selection else None
        if not team or line is None:
            return Settlement("void", "Nie udało się ustalić drużyny lub linii", 0.0)
        value = team_stat(team, "Corner Kicks")
        is_over = "over" in selection or "powyżej" in selection
        status = "won" if (value > line if is_over else value < line) else ("push" if value == line else "lost")
        return Settlement(status, f"{team}: {value} rożnych", _profit(status, odds))

    if market in {"cards", "kartki"}:
        team = home_name if home_name.lower() in selection else away_name if away_name.lower() in selection else None
        if not team or line is None:
            return Settlement("void", "Nie udało się ustalić drużyny lub linii", 0.0)
        value = team_stat(team, "Yellow Cards") + team_stat(team, "Red Cards")
        is_over = "over" in selection or "powyżej" in selection
        status = "won" if (value > line if is_over else value < line) else ("push" if value == line else "lost")
        return Settlement(status, f"{team}: {value} kartek", _profit(status, odds))

    return Settlement("void", f"Nieobsługiwany rynek: {selection_row['market']}", 0.0)
