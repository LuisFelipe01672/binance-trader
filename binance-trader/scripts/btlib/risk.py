"""Risk manager + circuit breaker. PASS or BLOCK for each proposed order, with the reason.

Circuit-breaker and revenge-trade rules adapted from tradermonty/claude-trading-skills
(drawdown-circuit-breaker, pre-trade-discipline-gate), MIT.
"""
from datetime import timedelta

from . import common as c
from . import data
from . import journal as J


def _day_start(now):
    b = now.astimezone(c.BRT)
    return b.replace(hour=0, minute=0, second=0, microsecond=0)


def account_state(cfg, now):
    # only this account: testnet results must never count against real-money limits (and vice versa)
    trades = [t for t in J.load()["trades"] if t.get("env", cfg["env"]) == cfg["env"]]
    day0 = _day_start(now)
    week0 = day0 - timedelta(days=day0.weekday())
    closed = [t for t in trades if t.get("closed")]
    pnl_day = sum(t["pnl"] for t in closed if c.from_ms(t["closed"]) >= day0)
    pnl_week = sum(t["pnl"] for t in closed if c.from_ms(t["closed"]) >= week0)
    opened_today = sum(1 for t in trades if c.from_ms(t["opened"]) >= day0 and not t.get("unfilled"))
    streak = 0
    for t in sorted(closed, key=lambda t: t["closed"], reverse=True):
        if t["pnl"] < 0:
            streak += 1
        else:
            break
    losses_today = [t for t in closed if t["pnl"] < 0 and c.from_ms(t["closed"]) >= day0]
    last_loss = max((t["closed"] for t in closed if t["pnl"] < 0), default=None)
    open_risk = sum(t.get("max_loss", 0) for t in trades if not t.get("closed"))
    open_cost = sum(float(t["qty"]) * float(t["entry"]) for t in trades if not t.get("closed"))
    return {"pnl_day": pnl_day, "pnl_week": pnl_week, "opened_today": opened_today,
            "streak": streak, "losses_today": len(losses_today), "last_loss": last_loss,
            "open_risk": open_risk, "open_cost": open_cost}


def check_order(o, cfg, st, now, regime=None, sim=None, last_price=None):
    """Returns (status, reasons, warnings)."""
    if o["kind"] == "close":
        return "PASS", ["ordem de saida: reduz risco"], []
    block, warn = [], []
    q = o["quote"]
    if o["max_loss"] > cfg["max_loss_per_trade"] + 1e-9:
        block.append("perda no stop %s acima do limite %s" % (c.money(o["max_loss"], q), c.money(cfg["max_loss_per_trade"], q)))
    if o["net_reward_risk"] < cfg["min_reward_risk"]:
        block.append("ganho:risco liquido %.2f abaixo do minimo %.2f" % (o["net_reward_risk"], cfg["min_reward_risk"]))
    if st["opened_today"] >= cfg["max_trades_per_day"]:
        block.append("ja foram %d trades hoje (limite %d)" % (st["opened_today"], cfg["max_trades_per_day"]))
    if -st["pnl_day"] + st["open_risk"] + o["max_loss"] > cfg["daily_loss_limit"] + 1e-9:
        block.append("o pior caso de hoje passaria do limite diario de %s (perdido %s, em risco aberto %s, este %s)" % (
            c.money(cfg["daily_loss_limit"], q), c.money(max(0, -st["pnl_day"]), q),
            c.money(st["open_risk"], q), c.money(o["max_loss"], q)))
    if -st["pnl_week"] >= cfg["weekly_loss_limit"]:
        block.append("limite semanal de perda atingido (%s)" % c.money(-st["pnl_week"], q))
    if st["streak"] >= cfg["losing_streak_stop"] and st["losses_today"] > 0:
        block.append("%d perdas seguidas: pausa ate amanha (losing_streak_stop)" % st["streak"])
    if st["last_loss"]:
        mins = (now - c.from_ms(st["last_loss"])).total_seconds() / 60
        if mins < cfg["revenge_cooldown_min"]:
            block.append("perda ha %.0f min: espere %d min para nao operar no impulso" % (mins, cfg["revenge_cooldown_min"]))
    budget = cfg["budget"]
    if o["notional"] > budget * cfg["max_position_pct"] / 100 + 1e-6:
        block.append("posicao de %s acima de %s%% do saldo" % (c.money(o["notional"], q), cfg["max_position_pct"]))
    if st["open_cost"] + o["notional"] > budget * (1 - cfg["cash_buffer_pct"] / 100) + 1e-6:
        block.append("sem caixa: %s ja em posicoes abertas" % c.money(st["open_cost"], q))
    if regime is None:
        warn.append("sem leitura de clima recente: rode regime --write")
    else:
        age_h = (now - c.from_ms(regime["time"])).total_seconds() / 3600
        if age_h > 12:
            warn.append("leitura de clima tem %.0fh: rode regime --write de novo" % age_h)
        elif regime["posture"] == "risco-desligado":
            (block if cfg["block_on_risk_off"] else warn).append("mercado em risco-desligado (BTC em queda)")
    fp_plan = c.fingerprint({"symbol": o["symbol"], "entry": o["entry"], "stop": o["stop"], "target": o["target"]})
    if not sim or sim.get("plan_fp") != fp_plan:
        warn.append("este plano nao passou pela maquina do tempo (sim --write)")
    else:
        edges = [sim["bootstrap"]["avg_r"]]
        if sim["history"].get("n", 0) >= 10:
            edges.append(sim["history"]["avg_r"])
        if min(edges) <= 0:
            (block if cfg["require_positive_edge"] else warn).append(
                "a simulacao da resultado medio <= 0R depois das taxas")
    if last_price is not None:
        e, s = float(o["entry"]), float(o["stop"])
        if last_price <= s:
            block.append("o preco (%s) ja esta no stop ou abaixo" % c.price_str(last_price, q))
        elif last_price > e * 1.005:
            warn.append("o preco ja subiu %s acima da entrada: a ordem limite pode nem executar; nao corra atras"
                        % c.pct((last_price / e - 1) * 100, 2))
    return ("BLOCK" if block else "PASS"), block, warn


def run(args, cfg):
    now = c.now(args.now)
    doc = c.read_json(args.orders or c.path("orders.json"), {"orders": []}) or {"orders": []}
    if not doc["orders"]:
        raise SystemExit("nenhuma ordem proposta: rode size primeiro")
    st = account_state(cfg, now)
    regime = c.read_json(c.path("regime.json"))
    sim = c.read_json(c.path("sim.json"))
    state = c.read_json(c.path("state", "risk.json"), {}) or {}
    passed = blocked = 0
    for n, o in enumerate(doc["orders"], 1):
        last = None
        if not args.no_price and o["kind"] != "close":
            try:
                last = data.price(o["symbol"])
            except c.HttpError as e:
                print("aviso: sem preco ao vivo (%s)" % e)
        if args.price is not None:
            last = args.price
        status, reasons, warns = check_order(o, cfg, st, now, regime, sim, last)
        if o["kind"] == "close":
            print("%s  #%d vender %s %s a mercado (saida)" % (status, n, o["qty"], o["symbol"]))
        else:
            print("%s  #%d comprar %s %s a %s, stop %s, alvo %s" % (
                status, n, o["qty"], o["symbol"], c.price_str(o["entry"], o["quote"]),
                c.price_str(o["stop"], o["quote"]), c.price_str(o["target"], o["quote"])))
            print("        perda no stop %s (limite %s), ganho:risco liquido %.2f (min %.2f)" % (
                c.money(o["max_loss"], o["quote"]), c.money(cfg["max_loss_per_trade"], o["quote"]),
                o["net_reward_risk"], cfg["min_reward_risk"]))
            print("        hoje: %d de %d trades, resultado %s, limite diario %s" % (
                st["opened_today"], cfg["max_trades_per_day"], c.money(st["pnl_day"], o["quote"]),
                c.money(cfg["daily_loss_limit"], o["quote"])))
        for r in reasons if status == "BLOCK" else []:
            print("        BLOQUEADO: " + r)
        for w in warns:
            print("        aviso: " + w)
        state[o["fingerprint"]] = {"status": status, "time": c.ms(now), "reasons": reasons}
        passed += status == "PASS"
        blocked += status == "BLOCK"
    c.write_json(c.path("state", "risk.json"), state)
    print("\n%d aprovada(s) pelas regras, %d bloqueada(s). Conta em %s (env da config)." % (
        passed, blocked, cfg["env"]))
    if passed:
        print("Mesmo aprovada, nada sai sem o seu \"sim\" no ticket.")
    print(c.DISCLAIMER)
