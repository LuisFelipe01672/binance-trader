"""Trades fantasmas: every signal the bot sees is followed as if it had been traded,
including the ones blocked by the weather, the simulation or the risk rules, and the ones
you declined. In a week or two this says whether the strategy (and your decisions) add value,
without risking anything.

Rules match the real order: LIMIT buy at the entry, valid entry_timeout_min; then stop and
target until max_hold_hours. Fees on both sides. When one candle touches both, stop first.
"""
from . import common as c
from . import data
from . import sim as SIM

OPEN = "aberto"


def load():
    return c.read_json(c.path("ghosts.json"), {"ghosts": []}) or {"ghosts": []}


def save(doc):
    c.write_json(c.path("ghosts.json"), doc)


def key_of(plan):
    return "%s|%s|%s" % (plan["symbol"], plan["setup"], plan["signal_time"])


def record(plan, decision, reason, cfg, now):
    """Adds the signal once (same coin, setup and candle = same ghost). Returns its key."""
    doc = load()
    k = key_of(plan)
    for g in doc["ghosts"]:
        if g["key"] == k:
            g.update(decision=decision, reason=reason)
            save(doc)
            return k
    iv_min = data.INTERVAL_MS[plan["interval"]] // 60000
    doc["ghosts"].append({
        "key": k, "id": len(doc["ghosts"]) + 1, "symbol": plan["symbol"], "quote": plan["quote"],
        "setup": plan["setup"], "interval": plan["interval"], "signal_time": plan["signal_time"],
        "entry": float(plan["entry"]), "stop": float(plan["stop"]), "target": float(plan["target"]),
        "fee_pct": plan["fee_pct"], "horizon_bars": plan["horizon_bars"],
        "entry_timeout_bars": max(1, int(cfg["entry_timeout_min"] // iv_min)),
        "decision": decision, "reason": reason, "created": c.ms(now), "status": OPEN})
    save(doc)
    return k


def set_decision(key, decision, reason=None):
    doc = load()
    for g in doc["ghosts"]:
        if g["key"] == key:
            g["decision"] = decision
            if reason is not None:
                g["reason"] = reason
    save(doc)


def resolve(g, bars):
    """Updates g from candles that opened after the signal. Leaves it open while undecided."""
    iv = data.INTERVAL_MS[g["interval"]]
    after = [b for b in bars if b["t"] >= g["signal_time"] + iv]
    e, s, t = g["entry"], g["stop"], g["target"]
    fee = g["fee_pct"] / 100
    window = after[:g["entry_timeout_bars"]]
    fill = next((j for j, b in enumerate(window) if b["l"] <= e), None)
    if fill is None:
        if len(after) >= g["entry_timeout_bars"]:
            g.update(status="nao executou", r=0.0, exit=None, resolved=window[-1]["ct"])
        return g
    fb = after[fill]
    if fb["l"] <= s:                               # filled and stopped in the same candle
        out, px, held = "stop", min(s, fb["o"]) if fb["o"] < e else s, 1
    else:
        out, px, held = SIM.walk(after, fill + 1, e, s, t, g["horizon_bars"])
        held += 1
        if out in ("tempo", "sem dados") and fill + 1 + g["horizon_bars"] > len(after):
            return g                               # still running
    g.update(status=out, exit=px, r=SIM.net_r(e, px, s, fee), filled=fb["t"],
             resolved=after[min(len(after), fill + held) - 1]["ct"])
    return g


def update_all(now, fetch=None):
    """Checks every open ghost against the candles so far. One download per coin."""
    fetch = fetch or (lambda sym, iv, n: data.klines(sym, iv, n))
    doc = load()
    groups = {}
    for g in doc["ghosts"]:
        if g["status"] == OPEN:
            groups.setdefault((g["symbol"], g["interval"]), []).append(g)
    changed = []
    for (sym, iv), gs in groups.items():
        first = min(g["signal_time"] for g in gs)
        n = min(1000, int((c.ms(now) - first) // data.INTERVAL_MS[iv]) + 5)
        try:
            bars = fetch(sym, iv, n)
        except c.HttpError:
            continue
        for g in gs:
            resolve(g, bars)
            if g["status"] != OPEN:
                changed.append(g)
    save(doc)
    return changed


def stats(ghosts):
    done = [g for g in ghosts if g["status"] not in (OPEN, "nao executou")]
    out = {"total": len(ghosts), "open": sum(1 for g in ghosts if g["status"] == OPEN),
           "unfilled": sum(1 for g in ghosts if g["status"] == "nao executou"), "n": len(done)}
    if done:
        out.update(win_rate=sum(1 for g in done if g["r"] > 0) / len(done),
                   avg_r=sum(g["r"] for g in done) / len(done), sum_r=sum(g["r"] for g in done))
    groups = {}
    for g in done:
        groups.setdefault(("setup", g["setup"]), []).append(g)
        groups.setdefault(("decisao", group_of(g["decision"])), []).append(g)
        groups.setdefault(("velas", "velas de %s" % g.get("interval", "15m")), []).append(g)
    out["groups"] = {k: {"n": len(v), "win_rate": sum(1 for g in v if g["r"] > 0) / len(v),
                         "avg_r": sum(g["r"] for g in v) / len(v)} for k, v in groups.items()}
    return out


def group_of(decision):
    return {"bloqueado": "bloqueado pelo risco", "sem vantagem": "sem vantagem na simulacao",
            "aprovado": "aprovado por voce", "recusado": "recusado por voce",
            "expirou": "voce nao respondeu", "proposto": "proposto",
            "desligado": "desligado pelo aprendizado"}.get(decision, decision)
