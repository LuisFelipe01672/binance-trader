"""Trade journal: what was opened, how it ended, P&L in reais. Feeds the risk manager
and exports a CSV you can hand to your accountant."""
import csv
from datetime import timedelta

from . import common as c


def load():
    return c.read_json(c.path("journal.json"), {"trades": []}) or {"trades": []}


def save(doc):
    c.write_json(c.path("journal.json"), doc)


def open_trade(order, now, env, broker_ref=None):
    doc = load()
    tid = max([t["id"] for t in doc["trades"]] + [0]) + 1
    doc["trades"].append({"id": tid, "symbol": order["symbol"], "base": order["base"], "quote": order["quote"],
                          "env": env, "opened": c.ms(now), "qty": order["qty"],
                          "sell_qty": order.get("sell_qty", order["qty"]), "entry": order["entry"],
                          "stop": order["stop"], "target": order["target"], "max_loss": order["max_loss"],
                          "setup": order.get("setup"), "broker_ref": broker_ref})
    save(doc)
    return tid


def finalize(tid, exit_px, cfg, now, entry_fill=None, fees=None, reason=None):
    """Close trade #tid at exit_px. Fees default to fee_pct on both sides."""
    doc = load()
    t = next((t for t in doc["trades"] if t["id"] == tid), None)
    if not t:
        raise SystemExit("trade #%s nao existe (journal show lista)" % tid)
    if t.get("closed"):
        raise SystemExit("trade #%s ja foi fechado" % tid)
    fee = cfg["fee_pct"] / 100
    entry = float(entry_fill or t.get("entry_fill") or t["entry"])
    buy = float(t["qty"]) * entry
    sell = float(t["sell_qty"]) * exit_px
    if fees is None:
        fees = (buy + sell) * fee
    pnl = sell - buy - fees
    t.update(closed=c.ms(now), exit=exit_px, entry_fill=entry, fees=fees, pnl=pnl,
             r=pnl / t["max_loss"] if t["max_loss"] else None, reason=reason)
    save(doc)
    return t


def cancel_unfilled(tid, now, reason="entrada nao executou"):
    """The entry never filled: nothing was bought, the trade ends at zero."""
    doc = load()
    for t in doc["trades"]:
        if t["id"] == tid and not t.get("closed"):
            t.update(closed=c.ms(now), exit=None, entry_fill=None, fees=0.0, pnl=0.0, r=0.0,
                     reason=reason, unfilled=True)
    save(doc)


def update(tid, **fields):
    doc = load()
    for t in doc["trades"]:
        if t["id"] == tid:
            t.update(fields)
    save(doc)


def close_trade(args, cfg):
    t = finalize(args.id, args.exit, cfg, c.now(args.now), args.entry_fill, args.fees, args.reason)
    print("Trade #%d fechado: %s, entrada %s, saida %s, taxas %s, resultado %s (%+.2fR)." % (
        t["id"], t["symbol"], c.price_str(t["entry_fill"], t["quote"]), c.price_str(args.exit, t["quote"]),
        c.money(t["fees"], t["quote"]), c.money(t["pnl"], t["quote"]), t["r"] or 0))
    if t["pnl"] < 0:
        print("Perda registrada: o verificador de risco agora exige %d min de pausa." % cfg["revenge_cooldown_min"])


def show(args, cfg):
    trades = load()["trades"]
    now = c.now(args.now)
    q = cfg["quote"]
    if not trades:
        print("Diario vazio.")
        return
    for t in trades[-args.last:]:
        st = ("aberto" if not t.get("closed") else "%s %+.2fR" % (c.money(t["pnl"], q), t["r"] or 0))
        print("#%d %s %s %s x %s @ %s  stop %s alvo %s  [%s, %s]" % (
            t["id"], c.fmt_time(t["opened"]), t["symbol"], t["qty"], t["base"],
            c.price_str(t["entry"], q), c.price_str(t["stop"], q), c.price_str(t["target"], q), t["env"], st))
    closed = [t for t in trades if t.get("closed")]
    if closed:
        day0 = now.astimezone(c.BRT).replace(hour=0, minute=0, second=0, microsecond=0)
        month0 = day0.replace(day=1)
        tot = lambda ts: sum(t["pnl"] for t in ts)
        wins = [t for t in closed if t["pnl"] > 0]
        print("\nFechados: %d | acerto %s | media %+.2fR | total %s" % (
            len(closed), c.pct(len(wins) / len(closed) * 100), sum(t["r"] or 0 for t in closed) / len(closed),
            c.money(tot(closed), q)))
        print("Hoje %s | ultimos 7 dias %s | mes %s" % (
            c.money(tot([t for t in closed if c.from_ms(t["closed"]) >= day0]), q),
            c.money(tot([t for t in closed if c.from_ms(t["closed"]) >= day0 - timedelta(days=7)]), q),
            c.money(tot([t for t in closed if c.from_ms(t["closed"]) >= month0]), q)))
        real = [t for t in closed if t["env"] == "prod" and c.from_ms(t["closed"]) >= month0]
        if real:
            sales = sum(float(t["sell_qty"]) * t["exit"] for t in real)
            print("Vendas reais no mes: %s. Imposto de renda sobre cripto tem regras proprias "
                  "(limite mensal de vendas, apuracao, declaracao): confira a regra vigente com um contador." % c.money(sales, q))


def export_csv(args, cfg):
    trades = [t for t in load()["trades"] if t.get("closed") and not t.get("unfilled")]
    out = args.out or c.path("diario.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["id", "ambiente", "abertura", "fechamento", "par", "quantidade_compra", "quantidade_venda",
                    "preco_compra", "preco_venda", "taxas", "resultado", "R", "motivo"])
        for t in trades:
            w.writerow([t["id"], t["env"], c.brt(t["opened"]).strftime("%d/%m/%Y %H:%M"),
                        c.brt(t["closed"]).strftime("%d/%m/%Y %H:%M"), t["symbol"], t["qty"], t["sell_qty"],
                        str(t["entry_fill"]).replace(".", ","), str(t["exit"]).replace(".", ","),
                        ("%.2f" % t["fees"]).replace(".", ","), ("%.2f" % t["pnl"]).replace(".", ","),
                        ("%.2f" % (t["r"] or 0)).replace(".", ","), t.get("reason") or ""])
    print("Exportado: %s (%d trades fechados, separador ';' para abrir no Excel em portugues)." % (out, len(trades)))
