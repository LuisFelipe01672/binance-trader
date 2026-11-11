"""Aprendizado: turns off what the ghosts show is losing, and back on when it recovers.

Looks at the most recent resolved ghosts (all of them, whatever was decided, since a ghost is
what the signal would have done). Two levels:
  - one setup on one coin on one candle size ("pullback|DOGEUSDT|15m");
  - one setup on every coin on one candle size ("pullback|*|15m"), with twice the samples.
A combination is off while the average of its last `learn_window` results is below
`learn_min_avg_r`. Off combinations keep being followed as ghosts, so they can come back.
"""
from . import ghost as G


def scores(ghosts, cfg):
    window = int(cfg.get("learn_window", 30))
    done = sorted([g for g in ghosts if g["status"] not in (G.OPEN, "nao executou")],
                  key=lambda g: g.get("resolved", 0))
    by = {}
    for g in done:
        iv = g.get("interval", "15m")
        by.setdefault("%s|%s|%s" % (g["setup"], g["symbol"], iv), []).append(g["r"])
        by.setdefault("%s|*|%s" % (g["setup"], iv), []).append(g["r"])
    out = {}
    for k, rs in by.items():
        recent = rs[-window * (2 if "|*|" in k else 1):]
        out[k] = {"n": len(recent), "avg_r": sum(recent) / len(recent)}
    return out


def off_set(ghosts, cfg):
    """{key: reason} for every combination that should be off now."""
    if not cfg.get("learn_enabled", True):
        return {}
    need = int(cfg.get("learn_min_samples", 15))
    floor = float(cfg.get("learn_min_avg_r", 0.0))
    off = {}
    for k, s in scores(ghosts, cfg).items():
        n_needed = need * (2 if "|*|" in k else 1)
        if s["n"] >= n_needed and s["avg_r"] < floor:
            off[k] = "%+.2fR medio nos ultimos %d sinais" % (s["avg_r"], s["n"])
    return off


def blocked(off, setup, symbol, interval):
    for k in ("%s|%s|%s" % (setup, symbol, interval), "%s|*|%s" % (setup, interval)):
        if k in off:
            return k, off[k]
    return None


def label(key):
    setup, sym, iv = key.split("|")
    return "%s em %s (velas de %s)" % (setup, "todas as moedas" if sym == "*" else sym, iv)
