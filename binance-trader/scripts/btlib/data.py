"""Public market data from Binance (data-api.binance.vision, no API key).

Every function takes an optional offline file so tests and replays need no network.
"""
import time

from . import common as c

INTERVAL_MS = {"1m": 60000, "3m": 180000, "5m": 300000, "15m": 900000, "30m": 1800000,
               "1h": 3600000, "2h": 7200000, "4h": 14400000, "1d": 86400000}


def _bar(k):
    return {"t": int(k[0]), "o": float(k[1]), "h": float(k[2]), "l": float(k[3]),
            "c": float(k[4]), "v": float(k[5]), "ct": int(k[6]), "qv": float(k[7])}


def klines(symbol, interval="15m", bars=500, end=None, offline=None, closed_only=True, now_ms=None, base=None):
    """Candles oldest -> newest as dicts {t,o,h,l,c,v,ct,qv}.

    end: datetime; only bars that opened before it. closed_only drops a bar still forming.
    """
    if interval not in INTERVAL_MS:
        raise SystemExit("intervalo invalido: %s (use %s)" % (interval, ", ".join(INTERVAL_MS)))
    end_ms = c.ms(end) if end else None
    if offline:
        raw = c.read_json(offline)
        if raw is None:
            raise SystemExit("arquivo offline nao encontrado: %s" % offline)
        out = [_bar(k) for k in raw]
        if end_ms is not None:
            out = [b for b in out if b["t"] < end_ms]
    else:
        out, cursor = [], (end_ms - 1) if end_ms else None
        while len(out) < bars + 1:
            p = {"symbol": symbol.upper(), "interval": interval, "limit": 1000}
            if cursor:
                p["endTime"] = cursor
            chunk = c.http("GET", (base or c.DATA_API) + "/api/v3/klines", p)
            if not chunk:
                break
            out = [_bar(k) for k in chunk] + out
            cursor = int(chunk[0][0]) - 1
            if len(chunk) < 1000:
                break
    ref = now_ms if now_ms is not None else (end_ms if end_ms else int(time.time() * 1000))
    if closed_only:
        out = [b for b in out if b["ct"] < ref]
    return out[-bars:] if bars else out


def price(symbol, offline_bars=None):
    if offline_bars:
        return offline_bars[-1]["c"]
    return float(c.http("GET", c.DATA_API + "/api/v3/ticker/price", {"symbol": symbol.upper()})["price"])


def ticker_24h():
    return c.http("GET", c.DATA_API + "/api/v3/ticker/24hr", timeout=40)


def _cache(name, fetch, max_age_h=24):
    p = c.path("cache", c.DATA_API.split("//")[1].split(".")[0] + "-" + name)   # testnet/demo/prod differ
    hit = c.read_json(p)
    if hit and time.time() - hit.get("_saved", 0) < max_age_h * 3600:
        return hit["data"]
    data = fetch()
    c.write_json(p, {"_saved": time.time(), "data": data})
    return data


def trading_symbols(quote="BRL", offline=None):
    """Symbols trading now against `quote`, stablecoins left out."""
    if offline:
        info = c.read_json(offline)
    else:
        info = _cache("exchangeInfo-all.json", lambda: c.http(
            "GET", c.DATA_API + "/api/v3/exchangeInfo",
            {"symbolStatus": "TRADING", "permissions": "SPOT"}, timeout=60))
    return [s["symbol"] for s in info["symbols"]
            if s["quoteAsset"] == quote and s.get("status", "TRADING") == "TRADING"
            and s["baseAsset"] not in c.STABLES]


def symbol_rules(symbol, offline=None):
    """tickSize, stepSize, minQty, minNotional, orderTypes for one symbol."""
    symbol = symbol.upper()
    if offline:
        info = c.read_json(offline)
    else:
        info = _cache("exchangeInfo-%s.json" % symbol, lambda: c.http(
            "GET", c.DATA_API + "/api/v3/exchangeInfo", {"symbol": symbol}))
    for s in info["symbols"]:
        if s["symbol"] == symbol:
            f = {x["filterType"]: x for x in s["filters"]}
            notional = f.get("NOTIONAL") or f.get("MIN_NOTIONAL") or {}
            return {
                "symbol": symbol, "base": s["baseAsset"], "quote": s["quoteAsset"],
                "status": s["status"],
                "tick": f["PRICE_FILTER"]["tickSize"],
                "step": f["LOT_SIZE"]["stepSize"],
                "min_qty": f["LOT_SIZE"]["minQty"],
                "min_notional": notional.get("minNotional", "0"),
                "order_types": s.get("orderTypes", []),
                "oto": s.get("otoAllowed", False), "oco": s.get("ocoAllowed", False),
            }
    raise SystemExit("par %s nao existe na Binance" % symbol)
