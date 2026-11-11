"""Setups (long only, spot). Each one looks at the CLOSED bar i and says whether it fired.

The same functions drive the scanner (last bar) and the history backtest (every bar),
so what you scan for is exactly what gets tested.
"""

SETUPS = {
    "rompimento": "fechamento acima da maxima das ultimas N velas, com volume acima da media",
    "pullback": "tendencia de alta (MME20 > MME50 subindo) e a vela toca a MME20 e fecha acima",
    "repique": "IFR(14) volta de baixo de 30 para cima, com vela verde",
}
DEFAULT_STOP = {"rompimento": "range-mid", "pullback": "atr:1.5", "repique": "swing-low"}
ALIASES = {"breakout": "rompimento", "orb": "rompimento", "pullback": "pullback",
           "oversold": "repique", "bounce": "repique"}


def norm(name):
    name = (name or "").lower()
    name = ALIASES.get(name, name)
    if name not in SETUPS:
        raise SystemExit("setup desconhecido: %s (use: %s)" % (name, ", ".join(SETUPS)))
    return name


def stop_price(rule, bars, ind, i, entry, ctx):
    """ctx: what the setup knows (range high/low). Returns a raw stop below entry, or None."""
    rule = rule or "atr:1.5"
    a = ind["atr"][i]
    if rule == "range-mid" and "range_lo" in ctx:
        s = (ctx["range_hi"] + ctx["range_lo"]) / 2
    elif rule == "range-low" and "range_lo" in ctx:
        s = ctx["range_lo"]
    elif rule == "swing-low":
        s = min(b["l"] for b in bars[max(0, i - 4):i + 1]) - 0.2 * (a or 0)
    elif rule.startswith("atr:"):
        if a is None:
            return None
        s = entry - float(rule[4:]) * a
    elif rule.startswith("pct:"):
        s = entry * (1 - float(rule[4:]) / 100)
    elif rule.startswith("price:"):
        s = float(rule[6:])
    else:
        raise SystemExit("regra de stop invalida: %s (range-mid, range-low, swing-low, atr:K, pct:X, price:X)" % rule)
    return s if s < entry else None


MIN_STOP_PCT = 0.6   # a stop closer than this is mostly fees: widen it


def detect(setup, bars, ind, i, lookback=24, vol_mult=1.5, stop_rule=None, min_stop_pct=MIN_STOP_PCT):
    if i < max(lookback + 1, 51) or i >= len(bars):
        return None
    b, p = bars[i], bars[i - 1]
    ctx, note = {}, ""
    if setup == "rompimento":
        win = bars[i - lookback:i]
        hi, lo = max(x["h"] for x in win), min(x["l"] for x in win)
        avg_v = sum(x["v"] for x in win) / lookback
        if not (b["c"] > hi and p["c"] <= hi and avg_v > 0 and b["v"] >= vol_mult * avg_v):
            return None
        ctx = {"range_hi": hi, "range_lo": lo}
        note = "fechou %.2f%% acima da maxima de %d velas, volume %.1fx a media" % (
            (b["c"] / hi - 1) * 100, lookback, b["v"] / avg_v)
    elif setup == "pullback":
        e20, e50, r = ind["ema20"], ind["ema50"], ind["rsi"]
        if None in (e20[i], e50[i], e50[i - 5], r[i]):
            return None
        if not (e20[i] > e50[i] > e50[i - 5] and b["l"] <= e20[i] < b["c"] and b["c"] > b["o"]
                and 40 <= r[i] <= 65):
            return None
        note = "tocou a MME20 (%.2f) e fechou acima, IFR %.0f" % (e20[i], r[i])
    elif setup == "repique":
        r = ind["rsi"]
        if r[i] is None or r[i - 1] is None:
            return None
        if not (r[i - 1] < 30 <= r[i] and b["c"] > b["o"]):
            return None
        note = "IFR saiu de %.0f para %.0f" % (r[i - 1], r[i])
    else:
        raise SystemExit("setup desconhecido: %s" % setup)
    entry = b["c"]
    stop = stop_price(stop_rule or DEFAULT_STOP[setup], bars, ind, i, entry, ctx)
    if stop is None:
        return None
    widened = False
    if min_stop_pct and stop > entry * (1 - min_stop_pct / 100):
        stop, widened = entry * (1 - min_stop_pct / 100), True
    out = {"widened": widened, "setup": setup, "i": i, "t": b["t"], "entry": entry, "stop": stop,
           "stop_rule": stop_rule or DEFAULT_STOP[setup], "note": note}
    out.update(ctx)
    return out
