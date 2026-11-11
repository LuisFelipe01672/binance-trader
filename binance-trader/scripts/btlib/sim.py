"""Time Machine: replays a planned trade thousands of times, plus the setup's real history.

Idea from Jake Schincariol's day-trader-skill (MIT), rebuilt for crypto: markets open 24/7,
so a replay runs for the trade's horizon (max_hold_hours), and every simulated candle is
drawn from the coin's own candles at the same time of day. Fees are charged on both sides.
"""
import random

from . import ind as I
from . import setups as S


def r_unit(entry, stop, fee):
    """Money lost per unit if the stop fills exactly, fees on entry and exit included."""
    return entry * (1 + fee) - stop * (1 - fee)


def net_r(entry, exit_px, stop, fee):
    return (exit_px * (1 - fee) - entry * (1 + fee)) / r_unit(entry, stop, fee)


def walk(bars, start, entry, stop, target, horizon):
    """Follow real bars from index start. Returns (outcome, exit_price, bars_held).

    Conservative: when one candle touches both, the stop counts first.
    A candle that opens past a level fills at its open (a gap).
    """
    last = min(len(bars), start + horizon)
    for j in range(start, last):
        b = bars[j]
        if b["l"] <= stop:
            return "stop", min(stop, b["o"]), j - start + 1
        if b["h"] >= target:
            return "alvo", max(target, b["o"]), j - start + 1
    if last <= start:
        return "sem dados", entry, 0
    return "tempo", bars[last - 1]["c"], last - start


def backtest(setup, bars, rr, horizon, fee, stop_rule=None, lookback=24, vol_mult=1.5, min_stop_pct=S.MIN_STOP_PCT):
    """Every time the setup fired in these bars: what happened, net of fees."""
    ind = I.compute(bars)
    trades, i = [], 0
    while i < len(bars) - 1:
        sig = S.detect(setup, bars, ind, i, lookback, vol_mult, stop_rule, min_stop_pct)
        if not sig:
            i += 1
            continue
        target = sig["entry"] + rr * (sig["entry"] - sig["stop"])
        if i + 1 + horizon > len(bars):
            break                                    # not enough future bars to judge it
        out, px, held = walk(bars, i + 1, sig["entry"], sig["stop"], target, horizon)
        trades.append({"t": sig["t"], "entry": sig["entry"], "stop": sig["stop"], "target": target,
                       "outcome": out, "exit": px, "bars": held,
                       "r": net_r(sig["entry"], px, sig["stop"], fee)})
        i += held + 1
    return summarize(trades)


def summarize(trades):
    n = len(trades)
    if not n:
        return {"n": 0, "trades": []}
    cnt = lambda k: sum(1 for t in trades if t["outcome"] == k)
    half = n // 2
    avg = lambda ts: sum(t["r"] for t in ts) / len(ts) if ts else None
    return {"n": n, "alvo": cnt("alvo"), "stop": cnt("stop"), "tempo": cnt("tempo"),
            "win_rate": sum(1 for t in trades if t["r"] > 0) / n, "avg_r": avg(trades),
            "avg_r_first_half": avg(trades[:half]), "avg_r_second_half": avg(trades[half:]),
            "trades": trades}


def bootstrap(history, entry_t, interval_ms, entry, stop, target, horizon, fee,
              n=10000, seed=7, window=2):
    """Monte Carlo: each replayed candle is a real candle (as ratios to the previous close)
    taken from the same time of day +- window candles."""
    day = 86400000
    slots = max(1, day // interval_ms)
    pools = {}
    for k in range(1, len(history)):
        pc = history[k - 1]["c"]
        if pc <= 0:
            continue
        b = history[k]
        s = (b["t"] % day) // interval_ms
        pools.setdefault(s, []).append((b["o"] / pc, b["h"] / pc, b["l"] / pc, b["c"] / pc))
    if not pools:
        raise SystemExit("historico vazio: nada para sortear")
    draw = []
    first = ((entry_t % day) // interval_ms + 1) % slots
    for j in range(horizon):
        s0 = (first + j) % slots
        pool = []
        for d in range(-window, window + 1):
            pool += pools.get((s0 + d) % slots, [])
        draw.append(pool or [r for p in pools.values() for r in p])
    rnd = random.Random(seed)
    res = {"alvo": 0, "stop": 0, "tempo": 0}
    finals, rs = [], []
    for _ in range(n):
        p, out, px = entry, "tempo", None
        for pool in draw:
            ro, rh, rl, rc = pool[rnd.randrange(len(pool))]
            o, h, l = p * ro, p * rh, p * rl
            if l <= stop:
                out, px = "stop", min(stop, o)
                break
            if h >= target:
                out, px = "alvo", max(target, o)
                break
            p = p * rc
        if px is None:
            px = p
        res[out] += 1
        finals.append(px)
        rs.append(net_r(entry, px, stop, fee))
    finals.sort()
    q = lambda f: finals[min(len(finals) - 1, int(f * len(finals)))]
    return {"n": n, "seed": seed, "alvo": res["alvo"] / n, "stop": res["stop"] / n,
            "tempo": res["tempo"] / n, "positive": sum(1 for r in rs if r > 0) / n,
            "avg_r": sum(rs) / n, "p05": q(0.05), "p50": q(0.5), "p95": q(0.95)}


def verdict(boot, hist):
    """Plain-language reading of the two tests."""
    edges = [boot["avg_r"]] + ([hist["avg_r"]] if hist and hist.get("n", 0) >= 10 else [])
    if hist and 0 < hist.get("n", 0) < 10:
        extra = " O historico tem so %d sinais: pouco para conclusao." % hist["n"]
    else:
        extra = ""
    if all(e > 0.15 for e in edges):
        return "VANTAGEM: os testes dao resultado medio positivo depois das taxas." + extra
    if all(e <= 0 for e in edges):
        return "SEM VANTAGEM: depois das taxas, o resultado medio e negativo. Melhor nao entrar." + extra
    return "CARA OU COROA: resultado medio perto de zero depois das taxas." + extra
