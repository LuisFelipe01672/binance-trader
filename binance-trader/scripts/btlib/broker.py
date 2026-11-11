"""Order executor. The only module that can touch your account, so it is the strictest.

An order goes out only when ALL of these hold, for that exact order (by fingerprint):
  1. the risk manager passed it today;
  2. you typed an explicit "sim" for it, less than approval_minutes ago (one sim = one order);
  3. for real money (env prod): allow_live true in config, BT_ALLOW_LIVE=1 in the environment
     and --live on the command line. Default env is testnet (fake money).
Keys come from the environment only: BINANCE_API_KEY and BINANCE_SECRET_KEY
(same names as Binance's official binance-cli). They are never printed or written.
"""
import hashlib
import hmac
import os
import re
import urllib.parse

from . import common as c
from . import journal as J

YES = {"sim", "s", "yes", "y", "confirmo", "pode", "pode enviar", "manda", "envia", "sim, envia",
       "sim envia", "sim pode", "pode mandar", "confirmado"}
NO_HINTS = ("nao", "não", "no", "talvez", "espera", "depois", "?")

ENDPOINT = {"otoco": "/api/v3/orderList/otoco", "close": "/api/v3/order"}


def is_yes(reply):
    r = re.sub(r"[!.\s]+$", "", (reply or "").strip().lower())
    if any(h == r or r.startswith(h + " ") for h in NO_HINTS) or "?" in r:
        return False
    return r in YES


def sign(query, secret):
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()


def keys():
    k = os.environ.get("BINANCE_API_KEY")
    s = os.environ.get("BINANCE_SECRET_KEY") or os.environ.get("BINANCE_API_SECRET")
    if not k or not s:
        raise SystemExit("faltam BINANCE_API_KEY e BINANCE_SECRET_KEY nas variaveis de ambiente. "
                         "Nunca cole as chaves no chat.")
    return k, s


def signed(method, env, endpoint, params, http=None):
    """Signed request. http is injectable for tests."""
    http = http or c.http
    base = c.ENV_URLS[env]
    k, s = keys()
    server = http("GET", base + "/api/v3/time")["serverTime"]
    p = dict(params)
    p.update(recvWindow=5000, timestamp=server)
    q = urllib.parse.urlencode(p)
    p["signature"] = sign(q, s)
    return http(method, base + endpoint, p, headers={"X-MBX-APIKEY": k})


def cli_equivalent(o):
    """Same order with Binance's official binance-cli, for whoever prefers it."""
    if o["kind"] == "close":
        p = o["params"]
        return "binance-cli spot new-order --symbol %s --side SELL --type MARKET --quantity %s" % (p["symbol"], p["quantity"])
    kebab = lambda k: re.sub(r"([A-Z])", lambda m: "-" + m.group(1).lower(), k)
    return "binance-cli spot order-list-otoco " + " ".join("--%s %s" % (kebab(k), v) for k, v in o["params"].items())


def _state():
    return c.read_json(c.path("state", "broker.json"), {"approvals": {}, "placed": {}}) or {"approvals": {}, "placed": {}}


def _orders():
    doc = c.read_json(c.path("orders.json"), {"orders": []}) or {"orders": []}
    return doc["orders"]


def _pick(index):
    orders = _orders()
    if not 1 <= index <= len(orders):
        raise SystemExit("ordem #%d nao existe (ha %d)" % (index, len(orders)))
    return orders[index - 1]


def _risk_ok(o, now):
    r = (c.read_json(c.path("state", "risk.json"), {}) or {}).get(o["fingerprint"])
    if not r or r["status"] != "PASS":
        return False, "o verificador de risco nao aprovou esta ordem (rode risk)"
    if c.brt(r["time"]).date() != c.brt(now).date():
        return False, "a aprovacao de risco e de outro dia (rode risk de novo)"
    return True, ""


def show(args, cfg):
    now = c.now(args.now)
    st = _state()
    orders = _orders()
    if not orders:
        print("Nenhuma ordem proposta.")
        return
    env = cfg["env"]
    print("Conta: %s%s" % (env.upper(), "  <<< DINHEIRO DE VERDADE >>>" if env == "prod" else " (dinheiro de mentira)"))
    for n, o in enumerate(orders, 1):
        q = o["quote"]
        print("\n#%d " % n + "-" * 60)
        if o["kind"] == "close":
            print("VENDER %s %s A MERCADO (sair da posicao)" % (o["qty"], o["base"]))
        else:
            print("COMPRAR %s %s por %s cada (ordem limite, ~%s)" % (
                o["qty"], o["base"], c.price_str(o["entry"], q), c.money(o["notional"], q)))
            print("  quando executar, a Binance coloca sozinha:")
            print("    ALVO  vender %s a %s  -> ganho ~%s" % (o["sell_qty"], c.price_str(o["target"], q), c.money(o["target_gain"], q)))
            print("    STOP  vender %s se cair a %s -> perda ~%s" % (o["sell_qty"], c.price_str(o["stop"], q), c.money(o["max_loss"], q)))
            print("    quando um executa, o outro e cancelado. Prazo do plano: %dh." % o["max_hold_hours"])
        if o["fingerprint"] in st["placed"]:
            print("  JA ENVIADA em %s (ref %s)" % (c.fmt_time(st["placed"][o["fingerprint"]]["time"]),
                                                 st["placed"][o["fingerprint"]].get("ref")))
            continue
        ok, why = _risk_ok(o, now)
        print("  %s" % ("PRONTA: risco aprovado, esperando o seu sim" if ok else "NAO PRONTA: " + why))
        print("  parametros exatos (POST %s):" % ENDPOINT[o["kind"]])
        for k, v in o["params"].items():
            print("    %s = %s" % (k, v))
        print("  mesmo pedido no binance-cli oficial:\n    %s" % cli_equivalent(o))
    print("\nResponda \"sim\" para enviar UMA ordem (approve --index N --reply \"sim\"). Um sim vale para uma ordem.")
    print(c.DISCLAIMER)


def approve(args, cfg):
    now = c.now(args.now)
    o = _pick(args.index)
    if not is_yes(args.reply):
        print("Resposta %r nao e um sim explicito. Nada aprovado." % args.reply)
        return False
    ok, why = _risk_ok(o, now)
    if not ok:
        print("Nao aprovado: " + why)
        return False
    st = _state()
    st["approvals"][o["fingerprint"]] = {"reply": args.reply, "time": c.ms(now), "index": args.index}
    c.write_json(c.path("state", "broker.json"), st)
    print("Sim registrado para a ordem #%d (%s), vale %d min. Proximo: place --index %d." % (
        args.index, o["symbol"], cfg["approval_minutes"], args.index))
    return True


def place(args, cfg, http=None):
    now = c.now(args.now)
    o = _pick(args.index)
    st = _state()
    fp = o["fingerprint"]
    env = cfg["env"]
    if fp in st["placed"]:
        raise SystemExit("esta ordem ja foi enviada (ref %s)" % st["placed"][fp].get("ref"))
    ok, why = _risk_ok(o, now)
    if not ok:
        raise SystemExit("BLOQUEADO: " + why)
    ap = st["approvals"].get(fp)
    if not ap:
        raise SystemExit("BLOQUEADO: falta o seu sim para esta ordem (approve)")
    if (c.ms(now) - ap["time"]) / 60000 > cfg["approval_minutes"]:
        raise SystemExit("BLOQUEADO: o sim tem mais de %d min; mostre o ticket e confirme de novo" % cfg["approval_minutes"])
    if env == "prod":
        locks = [cfg.get("allow_live") is True, os.environ.get("BT_ALLOW_LIVE") == "1", bool(args.live)]
        if not all(locks):
            raise SystemExit("BLOQUEADO: conta REAL exige as tres travas: \"allow_live\": true na config, "
                             "BT_ALLOW_LIVE=1 no ambiente e --live no comando.")
    if args.dry_run:
        print("SIMULADO (nada enviado): POST %s%s" % (c.ENV_URLS[env], ENDPOINT[o["kind"]]))
        for k, v in o["params"].items():
            print("  %s = %s" % (k, v))
        return None
    res = signed("POST", env, ENDPOINT[o["kind"]], o["params"], http=http)
    ref = res.get("orderListId", res.get("orderId"))
    st["placed"][fp] = {"time": c.ms(now), "env": env, "ref": ref, "index": args.index}
    del st["approvals"][fp]
    c.write_json(c.path("state", "broker.json"), st)
    if o["kind"] == "otoco":
        tid = J.open_trade(o, now, env, ref)
        print("Enviada para a Binance (%s): lista %s. Registrada no diario como trade #%d." % (env, ref, tid))
        print("Quando sair, registre: journal close --id %d --exit PRECO" % tid)
    else:
        print("Venda a mercado enviada (%s): ordem %s, status %s." % (env, ref, res.get("status")))
    return res


def status(args, cfg, http=None):
    env = cfg["env"]
    acct = signed("GET", env, "/api/v3/account", {"omitZeroBalances": "true"}, http=http)
    print("Conta %s, saldos:" % env.upper())
    for b in acct.get("balances", []):
        tot = float(b["free"]) + float(b["locked"])
        if tot > 0:
            print("  %-6s livre %s  bloqueado %s" % (b["asset"], b["free"], b["locked"]))
    lists = signed("GET", env, "/api/v3/openOrderList", {}, http=http)
    print("Listas de ordens abertas (OCO/OTOCO): %d" % len(lists))
    for l in lists:
        print("  %s lista %s: %s" % (l["symbol"], l["orderListId"], l.get("listOrderStatus")))
    opens = signed("GET", env, "/api/v3/openOrders", {}, http=http)
    for o in opens:
        print("  ordem %s %s %s %s qtd %s preco %s stop %s" % (o["orderId"], o["symbol"], o["side"], o["type"],
                                                            o["origQty"], o["price"], o.get("stopPrice")))


def cancel(args, cfg, http=None):
    if not is_yes(args.reply):
        raise SystemExit("cancelar remove o seu stop tambem. Confirme com --reply \"sim\".")
    sym = args.symbol.upper()
    if not sym.endswith(cfg["quote"]):
        sym += cfg["quote"]
    res = signed("DELETE", cfg["env"], "/api/v3/openOrders", {"symbol": sym}, http=http)
    print("Canceladas %d ordens abertas de %s (%s). Sua posicao, se houver, agora esta SEM stop." % (
        len(res) if isinstance(res, list) else 1, sym, cfg["env"]))
