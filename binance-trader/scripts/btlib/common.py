"""Shared helpers: config, state files, HTTP, time, number formatting.

Standard library only (Python 3.8+).
"""
import gzip
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, ROUND_UP, Decimal

PUBLIC_DATA_API = "https://data-api.binance.vision"   # public market data, no key
DATA_API = PUBLIC_DATA_API          # where candles and prices come from
ORDER_API = "https://testnet.binance.vision"   # market where orders go: tradable symbols and their rules
ENV_URLS = {
    "testnet": "https://testnet.binance.vision",       # fake money (default)
    "demo": "https://demo-api.binance.com",            # Binance demo trading, fake money
    "prod": "https://api.binance.com",                 # REAL money
}
UA = "binance-trader-skill/1.0"
BRT = timezone(timedelta(hours=-3), "BRT")           # Brazil has no DST since 2019
STABLES = {"USDT", "USDC", "BUSD", "FDUSD", "TUSD", "DAI", "USDP", "EUR", "BRL", "USD1", "RLUSD", "USDE",
           "PYUSD", "XUSD", "EURI", "AEUR", "USDS", "FRAX", "BFUSD", "PAXG", "XAUT"}

DEFAULT_CONFIG = {
    "quote": "BRL",                  # pairs quoted in reais: BTCBRL, ETHBRL...
    "budget": 1000.0,                # money in the account for this, in the quote asset
    "cash_buffer_pct": 10.0,         # never use the last 10% of the budget
    "max_loss_per_trade": 10.0,      # max loss at the stop, fees included
    "daily_loss_limit": 30.0,
    "weekly_loss_limit": 60.0,
    "max_trades_per_day": 3,
    "max_position_pct": 50.0,        # one position at most this % of the budget
    "min_reward_risk": 1.2,          # net of fees (a 2R target with a 0.6% stop nets ~1.24R)
    "losing_streak_stop": 2,         # N losses in a row -> no more trades today
    "revenge_cooldown_min": 30,      # minutes to wait after a loss
    "block_on_risk_off": True,       # regime risk-off blocks new longs
    "require_positive_edge": False,  # True: block if the simulation's net R <= 0
    "fee_pct": 0.1,                  # Binance spot default, per side
    "fee_in_bnb": False,             # True if you pay fees in BNB (then the base is not reduced)
    "min_stop_pct": 0.6,             # stops closer than this are widened (fees eat tight stops)
    "stop_limit_offset_pct": 0.3,    # STOP_LOSS_LIMIT limit price this % below the stop
    "max_hold_hours": 12,            # day-trade horizon: exit by then, win or lose
    "env": "testnet",                # testnet | demo | prod
    "allow_live": False,             # must be true (plus two more locks) for prod
    "approval_minutes": 15,          # a "sim" is valid this long
    "market_data": "real",           # real: signals, simulations and ghosts read the REAL market (public data)
                                     # even on testnet, whose prices are thin and artificial; "env": the env's own
    "watchlist": [],                 # ["BTC", "ETH"...], "auto" (most traded), or empty = every pair
    "auto_top": 15,                  # how many coins the "auto" watchlist keeps
    "min_volume_24h": 500000,        # scan skips pairs with less volume (quote asset)
    "entry_timeout_min": 60,         # bot cancels an entry order not filled by then
    "bot_skip_no_edge": True,        # bot does not propose trades the simulation calls SEM VANTAGEM
    "bot_interval": "15m",           # (old single setting, used when bot_intervals is empty)
    "bot_intervals": ["15m", "1h"],  # candle sizes the bot scans; ghosts compare them
    "learn_enabled": True,           # turn off setups the ghosts show are losing
    "learn_min_samples": 15,         # results needed before judging (twice that for a setup on all coins)
    "learn_window": 30,              # judge on the most recent results only
    "learn_min_avg_r": 0.0,          # off while the recent average is below this
    "dashboard_port": 8765,          # painel em http://localhost:8765 no proprio aparelho
    "daily_summary_hour": 21,        # resumo diario no Telegram a esta hora (Brasilia); -1 desliga
}


# ---------------------------------------------------------------- paths/state
def home():
    """Folder where everything is written (default ./bt in the project)."""
    return os.environ.get("BT_HOME") or os.path.join(os.getcwd(), "bt")


def path(*parts):
    return os.path.join(home(), *parts)


def read_json(p, default=None):
    try:
        if p.endswith(".gz"):
            with gzip.open(p, "rt", encoding="utf-8") as f:
                return json.load(f)
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def write_json(p, data):
    os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, p)


def load_config(p=None):
    cfg = dict(DEFAULT_CONFIG)
    user = read_json(p or path("config.json"), {}) or {}
    cfg.update({k: v for k, v in user.items() if not k.startswith("_")})
    if cfg["env"] not in ENV_URLS:
        raise SystemExit("config env must be testnet, demo or prod, not %r" % cfg["env"])
    global DATA_API, ORDER_API
    ORDER_API = PUBLIC_DATA_API if cfg["env"] == "prod" else ENV_URLS[cfg["env"]]   # prod: same market, public mirror
    md = cfg["market_data"]
    if md in ("real", "auto"):
        DATA_API = PUBLIC_DATA_API
    elif md == "env":
        DATA_API = ENV_URLS[cfg["env"]] if cfg["env"] != "prod" else PUBLIC_DATA_API
    else:
        DATA_API = md
    return cfg


# ---------------------------------------------------------------- time
def now(override=None):
    """Aware UTC datetime. override: 'YYYY-MM-DD HH:MM' (Brasilia time) or ISO."""
    if override:
        s = override.strip()
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            dt = datetime.strptime(s, "%Y-%m-%d %H:%M")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=BRT)
        return dt.astimezone(timezone.utc)
    return datetime.now(timezone.utc)


def ms(dt):
    return int(dt.timestamp() * 1000)


def from_ms(t):
    return datetime.fromtimestamp(t / 1000, timezone.utc)


def brt(dt_or_ms):
    dt = from_ms(dt_or_ms) if isinstance(dt_or_ms, (int, float)) else dt_or_ms
    return dt.astimezone(BRT)


def fmt_time(dt_or_ms):
    return brt(dt_or_ms).strftime("%d/%m %H:%M")


# ---------------------------------------------------------------- HTTP
class HttpError(Exception):
    def __init__(self, status, body):
        self.status, self.body = status, body
        super().__init__("HTTP %s: %s" % (status, body[:300]))


def http(method, url, params=None, headers=None, timeout=20, retries=2):
    """Returns parsed JSON. Retries 429/5xx politely."""
    q = urllib.parse.urlencode(params or {}, doseq=True)
    full = url + ("?" + q if q and method in ("GET", "DELETE") else "")
    data = q.encode() if method in ("POST", "PUT") else None
    h = {"User-Agent": UA, "Accept": "application/json"}
    h.update(headers or {})
    for attempt in range(retries + 1):
        req = urllib.request.Request(full, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode() or "null")
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            if e.code == 451:
                raise HttpError(451, "a Binance bloqueia o pais de onde esta conexao sai "
                                     "(restricted location). Rode no seu computador, no Brasil.")
            if (e.code == 429 or e.code >= 500) and attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            raise HttpError(e.code, body)
        except urllib.error.URLError as e:
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            raise HttpError(0, "sem conexao com %s: %s" % (url.split("/")[2], e.reason))


# ---------------------------------------------------------------- numbers
def D(x):
    return x if isinstance(x, Decimal) else Decimal(str(x))


def floor_step(x, step):
    step = D(step)
    return (D(x) / step).to_integral_value(ROUND_DOWN) * step


def ceil_step(x, step):
    step = D(step)
    return (D(x) / step).to_integral_value(ROUND_UP) * step


def dstr(x):
    """Decimal -> plain string without exponent or trailing zeros ('0.00012')."""
    s = format(D(x).normalize(), "f")
    return s


def money(x, quote="BRL", nd=2):
    """R$ 1.234,56 for BRL, '1,234.56 USDT' otherwise."""
    x = float(x)
    if quote == "BRL":
        s = "{:,.{}f}".format(abs(x), nd).replace(",", "X").replace(".", ",").replace("X", ".")
        return ("-" if x < 0 else "") + "R$\u00a0" + s
    return "{:,.{}f}\u00a0{}".format(x, nd, quote)


def price_str(x, quote="BRL"):
    x = float(x)
    nd = 2 if x >= 1 else (6 if x >= 0.001 else 10)
    if quote == "BRL":
        return money(x, "BRL", nd)
    return "{:.{}f}".format(x, nd)


def pct(x, nd=1):
    return ("{:.%df}" % nd).format(x).replace(".", ",") + "\u00a0%"      # no line break before %


def fingerprint(obj):
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def split_symbol(symbol, quote):
    symbol = symbol.upper()
    if symbol.endswith(quote):
        return symbol[: -len(quote)], quote
    return symbol, quote


DISCLAIMER = "Ferramenta educacional, nao e recomendacao de investimento."
