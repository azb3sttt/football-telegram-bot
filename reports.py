from collections import defaultdict


def summarize(rows):
    rows = list(rows)
    settled = [r for r in rows if r["status"] in ("won", "lost", "push", "void")]
    staked = sum(1 for r in settled if r["status"] != "void")
    profit = sum(float(r["profit_loss"] or 0) for r in settled)
    wins = sum(1 for r in settled if r["status"] == "won")
    losses = sum(1 for r in settled if r["status"] == "lost")
    pushes = sum(1 for r in settled if r["status"] == "push")
    avg_odds = (
        sum(float(r["odds"]) for r in settled if r["status"] != "void") / staked
        if staked else 0
    )
    avg_conf = (
        sum(float(r["confidence"] or 0) for r in settled) / len(settled)
        if settled else 0
    )
    yield_pct = (profit / staked * 100.0) if staked else 0.0
    hit_rate = (wins / (wins + losses) * 100.0) if (wins + losses) else 0.0
    return dict(
        count=len(settled), wins=wins, losses=losses, pushes=pushes,
        staked=float(staked), profit=profit, yield_=yield_pct, roi=yield_pct,
        hit_rate=hit_rate, avg_odds=avg_odds, avg_conf=avg_conf,
    )


def summarize_coupons(rows):
    rows = list(rows)
    staked = sum(float(r["stake_units"] or 0) for r in rows if r["status"] != "void")
    profit = sum(float(r["profit_loss"] or 0) for r in rows)
    wins = sum(1 for r in rows if r["status"] == "won")
    losses = sum(1 for r in rows if r["status"] == "lost")
    pushes = sum(1 for r in rows if r["status"] == "push")
    y = (profit / staked * 100.0) if staked else 0.0
    return dict(count=len(rows), wins=wins, losses=losses, pushes=pushes,
                staked=staked, profit=profit, yield_=y)


def by_market(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row["market"] or "Inne"].append(row)
    return {k: summarize(v) for k, v in groups.items()}


def format_report(title, rows):
    s = summarize(rows)
    return (
        f"📊 {title}\n\n"
        f"🎯 Rozliczone selekcje: {s['count']}\n"
        f"✅ Trafione: {s['wins']}\n"
        f"❌ Nietrafione: {s['losses']}\n"
        f"↩️ Zwroty: {s['pushes']}\n\n"
        f"Skuteczność: {s['hit_rate']:.2f}%\n"
        f"💰 Stawka modelowa: {s['staked']:.2f} units (1u / selekcję)\n"
        f"📈 Profit: {s['profit']:+.2f} units\n"
        f"📊 Yield: {s['yield_']:+.2f}%\n"
        f"Średni kurs: {s['avg_odds']:.2f}\n"
        f"Średni confidence: {s['avg_conf']:.0f}/100"
    )


def format_coupon_report(title, rows):
    s = summarize_coupons(rows)
    return (
        f"🎟 {title}\n\n"
        f"Kupony: {s['count']}\n"
        f"✅ Trafione: {s['wins']}\n"
        f"❌ Nietrafione: {s['losses']}\n"
        f"↩️ Zwroty: {s['pushes']}\n"
        f"💰 Postawiono: {s['staked']:.2f} units\n"
        f"📈 Bilans AKO: {s['profit']:+.2f} units\n"
        f"📊 Yield AKO: {s['yield_']:+.2f}%"
    )
