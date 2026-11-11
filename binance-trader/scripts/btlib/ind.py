"""Indicators on plain lists (None until enough data)."""


def ema(xs, n):
    out, k, e = [], 2.0 / (n + 1), None
    for i, x in enumerate(xs):
        if i + 1 < n:
            out.append(None)
            continue
        e = sum(xs[i + 1 - n:i + 1]) / n if e is None else x * k + e * (1 - k)
        out.append(e)
    return out


def sma(xs, n):
    return [None if i + 1 < n else sum(xs[i + 1 - n:i + 1]) / n for i in range(len(xs))]


def rsi(closes, n=14):
    """Wilder's RSI."""
    out = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    ag, al = gains / n, losses / n
    out[n] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (n - 1) + max(d, 0)) / n
        al = (al * (n - 1) + max(-d, 0)) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def atr(bars, n=14):
    """Wilder's ATR."""
    out = [None] * len(bars)
    trs = []
    for i, b in enumerate(bars):
        tr = b["h"] - b["l"] if i == 0 else max(b["h"] - b["l"], abs(b["h"] - bars[i - 1]["c"]),
                                                 abs(b["l"] - bars[i - 1]["c"]))
        trs.append(tr)
        if i + 1 == n:
            out[i] = sum(trs) / n
        elif i + 1 > n:
            out[i] = (out[i - 1] * (n - 1) + tr) / n
    return out


def compute(bars):
    closes = [b["c"] for b in bars]
    return {"ema20": ema(closes, 20), "ema50": ema(closes, 50), "ema200": ema(closes, 200),
            "rsi": rsi(closes, 14), "atr": atr(bars, 14), "vol20": sma([b["v"] for b in bars], 20)}
