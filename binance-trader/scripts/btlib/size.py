"""Position size from your max loss, then the exact OTOCO order for Binance.

OTOCO = One-Triggers-a-One-Cancels-the-Other: a LIMIT buy at the entry; once it fills,
Binance itself places the take-profit and the stop, and when one fills the other is
cancelled. So the stop exists from the first second the position does.
"""
from . import common as c
from . import data


def pnl_at(px, qty, sell_qty, entry, cfg):
    """Result if the position exits at px, fees included. With fees in the coin, what is
    left over after rounding the sell quantity (dust) stays in the account, valued at px."""
    fee = cfg["fee_pct"] / 100
    if cfg["fee_in_bnb"]:
        return qty * (px - entry) - (qty * entry + qty * px) * fee
    dust = qty * (1 - fee) - sell_qty
    return sell_qty * px * (1 - fee) + dust * px - qty * entry


def build_otoco(p, qty, rules, cfg):
    fee = c.D(cfg["fee_pct"]) / 100
    step = rules["step"]
    tick = rules["tick"]
    # Buying charges the fee in the coin you buy (unless you pay fees in BNB):
    # 0.01 BTC bought arrives as 0.00999 BTC. Selling 0.01 would fail for lack of balance.
    sell_qty = qty if cfg["fee_in_bnb"] else c.floor_step(qty * (1 - fee), step)
    stop = c.D(p["stop"])
    below = {"pendingBelowType": "STOP_LOSS", "pendingBelowStopPrice": c.dstr(stop)}
    if "STOP_LOSS" not in rules["order_types"]:
        limit = c.floor_step(stop * (1 - c.D(cfg["stop_limit_offset_pct"]) / 100), tick)
        below = {"pendingBelowType": "STOP_LOSS_LIMIT", "pendingBelowStopPrice": c.dstr(stop),
                 "pendingBelowPrice": c.dstr(limit), "pendingBelowTimeInForce": "GTC"}
    params = {
        "symbol": p["symbol"],
        "workingType": "LIMIT", "workingSide": "BUY", "workingTimeInForce": "GTC",
        "workingPrice": p["entry"], "workingQuantity": c.dstr(qty),
        "pendingSide": "SELL", "pendingQuantity": c.dstr(sell_qty),
        "pendingAboveType": "LIMIT_MAKER", "pendingAbovePrice": p["target"],
    }
    params.update(below)
    return params, sell_qty


def size(args, cfg):
    p = c.read_json(args.plan or c.path("plan.json"))
    if not p:
        raise SystemExit("sem plano: rode plan primeiro")
    quote = p["quote"]
    rules = data.symbol_rules(p["symbol"], offline=args.rules)
    budget = float(args.budget if args.budget is not None else cfg["budget"])
    max_loss = float(cfg["max_loss_per_trade"])
    fee = cfg["fee_pct"] / 100
    e, s = float(p["entry"]), float(p["stop"])
    unit = e * (1 + fee) - s * (1 - fee)
    usable = budget * (1 - cfg["cash_buffer_pct"] / 100)
    cap_pos = budget * cfg["max_position_pct"] / 100
    q_loss = max_loss / unit
    q_cash = usable / (e * (1 + fee))
    q_pos = cap_pos / e
    raw = min(q_loss, q_cash, q_pos)
    bound = {q_loss: "sua perda maxima por trade", q_cash: "o dinheiro disponivel",
             q_pos: "o limite de %s%% do saldo por posicao" % cfg["max_position_pct"]}[raw]
    qty = c.floor_step(raw, rules["step"])
    notional = float(qty) * e
    print("Tamanho: %s, conta %s, perda maxima %s por trade (taxas incluidas)." % (
        p["symbol"], c.money(budget, quote), c.money(max_loss, quote)))
    print("  pela perda maxima  %s / %s por unidade = %s %s" % (
        c.money(max_loss, quote), c.price_str(unit, quote), c.dstr(c.floor_step(q_loss, rules['step'])), p["base"]))
    print("  pelo caixa         %s (saldo menos %s%% de reserva) = %s %s" % (
        c.money(usable, quote), cfg["cash_buffer_pct"], c.dstr(c.floor_step(q_cash, rules['step'])), p["base"]))
    print("  pelo limite/posicao %s = %s %s" % (c.money(cap_pos, quote), c.dstr(c.floor_step(q_pos, rules['step'])), p["base"]))
    problems = []
    if qty <= 0 or qty < c.D(rules["min_qty"]):
        problems.append("quantidade %s abaixo do minimo da Binance (%s)" % (c.dstr(qty), c.dstr(rules["min_qty"])))
    if notional < float(rules["min_notional"]):
        problems.append("ordem de %s abaixo do minimo de %s da Binance" % (
            c.money(notional, quote), c.money(float(rules["min_notional"]), quote)))
    if not rules.get("oto") or not rules.get("oco"):
        problems.append("%s nao aceita ordens OTO/OCO" % p["symbol"])
    if problems:
        for pr in problems:
            print("  NAO DA: " + pr)
        print("Aumente a perda maxima, o saldo, ou use um stop mais curto. Nenhuma ordem gravada.")
        c.write_json(args.write or c.path("orders.json"), {"orders": []})
        return None
    params, sell_qty = build_otoco(p, qty, rules, cfg)
    pnl = lambda px: pnl_at(px, float(qty), float(sell_qty), e, cfg)
    max_loss_real = -pnl(s)
    target_gain = pnl(float(p["target"]))
    print("\nPosicao: %s %s = %s (limitado por %s)." % (c.dstr(qty), p["base"], c.money(notional, quote), bound))
    print("  se bater o stop: perda de ~%s  |  se bater o alvo: ganho de ~%s (taxas incluidas)" % (
        c.money(max_loss_real, quote), c.money(target_gain, quote)))
    if sell_qty != qty:
        print("  a venda e de %s, nao %s: a taxa da compra sai na propria moeda; a sobra fica na conta." % (
            c.dstr(sell_qty), c.dstr(qty)))
    print("  o stop e uma ordem, nao uma promessa: num tombo rapido ele pode sair pior.")
    order = {"kind": "otoco", "symbol": p["symbol"], "base": p["base"], "quote": quote,
             "qty": c.dstr(qty), "sell_qty": c.dstr(sell_qty), "entry": p["entry"], "stop": p["stop"],
             "target": p["target"], "notional": notional, "max_loss": max_loss_real,
             "target_gain": target_gain, "net_reward_risk": target_gain / max_loss_real,
             "setup": p["setup"], "max_hold_hours": p["max_hold_hours"], "params": params}
    order["fingerprint"] = c.fingerprint(params)
    c.write_json(args.write or c.path("orders.json"), {"created": c.ms(c.now(args.now)), "orders": [order]})
    print("\nOrdem proposta gravada em %s (nada foi enviado)." % (args.write or c.path("orders.json")))
    print("Proximo: risk confere seus limites, depois ticket mostra o boleto.")
    return order


def close_order(args, cfg):
    """A market SELL to get out of a position (flat by the deadline, or by choice)."""
    quote = cfg["quote"]
    sym = args.symbol.upper()
    if not sym.endswith(quote):
        sym += quote
    rules = data.symbol_rules(sym, offline=args.rules)
    qty = c.floor_step(args.qty, rules["step"])
    params = {"symbol": sym, "side": "SELL", "type": "MARKET", "quantity": c.dstr(qty)}
    order = {"kind": "close", "symbol": sym, "base": rules["base"], "quote": quote, "qty": c.dstr(qty),
             "params": params, "fingerprint": c.fingerprint(params)}
    doc = c.read_json(c.path("orders.json"), {"orders": []}) or {"orders": []}
    doc["orders"].append(order)
    c.write_json(c.path("orders.json"), doc)
    print("Ordem de saida gravada: vender %s %s a mercado (#%d). Precisa do seu sim no ticket." % (
        c.dstr(qty), rules["base"], len(doc["orders"])))
    print("Antes, cancele o OCO aberto (stop e alvo), senao a Binance pode vender duas vezes: "
          "cancel --symbol %s" % sym)
    return order
