"""regime, scan, plan, sim: everything that reads the market and writes a plan."""
from . import common as c
from . import data
from . import ind as I
from . import setups as S
from . import sim as SIM


# ---------------------------------------------------------------- regime
def regime(args, cfg):
    """Market weather from BTCUSDT daily candles: is it a day to buy dips or to sit out?"""
    bars = data.klines("BTCUSDT", "1d", 260, offline=args.offline, end=c.now(args.now) if args.now else None)
    if len(bars) < 200:
        raise SystemExit("poucos dados diarios do BTC (%d)" % len(bars))
    cl = [b["c"] for b in bars]
    e50, e200 = I.ema(cl, 50)[-1], I.ema(cl, 200)[-1]
    last = cl[-1]
    ch7, ch30 = (last / cl[-8] - 1) * 100, (last / cl[-31] - 1) * 100
    rets = [abs(cl[k] / cl[k - 1] - 1) for k in range(len(cl) - 20, len(cl))]
    vol = sum(rets) / len(rets) * 100
    checks = [
        ("BTC acima da MME200 diaria", last > e200),
        ("MME50 acima da MME200", e50 > e200),
        ("BTC acima da MME50 diaria", last > e50),
        ("BTC subiu nos ultimos 7 dias", ch7 > 0),
    ]
    score = sum(1 if ok else -1 for _, ok in checks)
    posture = "risco-ligado" if score >= 2 else ("risco-desligado" if score <= -2 else "neutro")
    out = {"time": c.ms(c.now(args.now)), "posture": posture, "score": score, "btc": last,
           "ema50": e50, "ema200": e200, "change_7d": ch7, "change_30d": ch30,
           "avg_daily_move": vol}
    print("Clima do mercado cripto (BTC/USDT diario, ultima vela fechada %s):" % c.fmt_time(bars[-1]["t"]))
    for name, ok in checks:
        print("  [%s] %s" % ("x" if ok else " ", name))
    print("  BTC %.0f  |  7 dias %s  |  30 dias %s  |  movimento medio diario %s" % (
        last, c.pct(ch7), c.pct(ch30), c.pct(vol)))
    print("\nPostura: %s (placar %+d de 4)." % (posture.upper(), score))
    if posture == "risco-desligado":
        print("Mercado em queda: compras contra a tendencia costumam dar errado. "
              "O verificador de risco bloqueia novas compras (block_on_risk_off).")
    elif posture == "neutro":
        print("Mercado sem direcao clara: reduza o tamanho e seja seletivo.")
    else:
        print("Mercado a favor de compras. Isso nao garante nenhum trade.")
    if args.write is not None:
        c.write_json(args.write or c.path("regime.json"), out)
        print("Gravado em %s." % (args.write or c.path("regime.json")))
    return out


# ---------------------------------------------------------------- scan
def scan(args, cfg):
    quote = cfg["quote"]
    setups = [S.norm(s) for s in (args.setup.split(",") if args.setup else S.SETUPS)]
    if args.min_volume is None:
        args.min_volume = cfg["min_volume_24h"]
    if not args.symbols and cfg.get("watchlist"):
        args.symbols = ",".join(cfg["watchlist"])
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]
        symbols = [s if s.endswith(quote) else s + quote for s in symbols]
    else:
        symbols = data.trading_symbols(quote)
    vols = {}
    if not args.offline_dir:
        try:
            vols = {t["symbol"]: float(t["quoteVolume"]) for t in data.ticker_24h()
                    if t["symbol"] in symbols}
        except c.HttpError as e:
            print("aviso: volume de 24h indisponivel (%s)" % e)
    hits, skipped = [], []
    end = c.now(args.now) if args.now else None
    for sym in symbols:
        if vols and vols.get(sym, 0) < args.min_volume:
            skipped.append(sym)
            continue
        try:
            off = None
            if args.offline_dir:
                import os
                off = os.path.join(args.offline_dir, "klines-%s-%s.json.gz" % (sym, args.interval))
                if not os.path.exists(off):
                    continue
            bars = data.klines(sym, args.interval, 300, offline=off, end=end)
        except c.HttpError as e:
            print("aviso: %s: %s" % (sym, e))
            continue
        if len(bars) < 60:
            continue
        ind = I.compute(bars)
        for setup in setups:
            for i in range(len(bars) - args.recent, len(bars)):
                sig = S.detect(setup, bars, ind, i, args.lookback, args.vol_mult, None, cfg["min_stop_pct"])
                if sig:
                    sig.update(symbol=sym, last=bars[-1]["c"], bars_ago=len(bars) - 1 - i,
                               volume_24h=vols.get(sym))
                    hits.append(sig)
    hits.sort(key=lambda h: (h["bars_ago"], -(h.get("volume_24h") or 0)))
    stamp = c.fmt_time(c.now(args.now))
    print("Varredura %s: %d pares em %s, velas de %s, setups: %s." % (
        stamp, len(symbols) - len(skipped), quote, args.interval, ", ".join(setups)))
    if skipped:
        print("Fora por volume baixo (< %s em 24h): %s" % (c.money(args.min_volume, quote, 0), ", ".join(skipped)))
    if not hits:
        print("\nNenhum sinal agora. Nao ter trade tambem e uma decisao.")
    for n, h in enumerate(hits, 1):
        risk = (h["entry"] - h["stop"]) / h["entry"] * 100
        print("\n%d. %s  %s  (%s)" % (n, h["symbol"], h["setup"].upper(),
                                     "vela que acabou de fechar" if h["bars_ago"] == 0 else "%d velas atras" % h["bars_ago"]))
        print("   %s%s" % (h["note"], "; stop alargado ao minimo de %s" % c.pct(cfg["min_stop_pct"]) if h.get("widened") else ""))
        print("   entrada %s  stop %s (%s, risco %s)  preco agora %s" % (
            c.price_str(h["entry"], quote), c.price_str(h["stop"], quote), h["stop_rule"],
            c.pct(risk, 2), c.price_str(h["last"], quote)))
    c.write_json(c.path("scan.json"), {"time": c.ms(c.now(args.now)), "interval": args.interval,
                                        "lookback": args.lookback, "vol_mult": args.vol_mult, "hits": hits})
    print("\nProximo: plan --symbol X --setup Y monta stop e alvo; sim testa antes de arriscar.")


# ---------------------------------------------------------------- plan
def plan(args, cfg):
    quote = cfg["quote"]
    sym = args.symbol.upper()
    if not sym.endswith(quote):
        sym += quote
    rules = data.symbol_rules(sym, offline=args.rules)
    end = c.now(args.now) if args.now else None
    bars = data.klines(sym, args.interval, 300, offline=args.offline, end=end)
    ind = I.compute(bars)
    fee = cfg["fee_pct"] / 100
    sig = None
    if args.setup:
        setup = S.norm(args.setup)
        for i in range(len(bars) - 1, len(bars) - 1 - args.recent, -1):
            sig = S.detect(setup, bars, ind, i, args.lookback, args.vol_mult, args.stop, cfg["min_stop_pct"])
            if sig:
                break
        if not sig and args.entry is None:
            raise SystemExit("%s: o setup %s nao disparou nas ultimas %d velas de %s. "
                             "Nada para planejar (ou passe --entry e --stop manualmente)."
                             % (sym, setup, args.recent, args.interval))
    if args.entry is not None:
        entry = args.entry
        if sig is None:
            if not args.stop:
                raise SystemExit("plano manual precisa de --stop (price:X, pct:X, atr:K)")
            stop = S.stop_price(args.stop, bars, ind, len(bars) - 1, entry, {})
            if stop is None:
                raise SystemExit("o stop precisa ficar abaixo da entrada")
            sig = {"setup": "manual", "t": bars[-1]["t"], "stop_rule": args.stop, "note": "plano manual"}
        else:
            stop = S.stop_price(args.stop or sig["stop_rule"], bars, ind, sig["i"], entry, sig)
            if stop is None:
                raise SystemExit("o stop precisa ficar abaixo da entrada")
            stop = min(stop, entry * (1 - cfg["min_stop_pct"] / 100))
    else:
        entry, stop = sig["entry"], sig["stop"]
    tick = rules["tick"]
    entry_d = c.floor_step(entry, tick)
    stop_d = c.floor_step(stop, tick)
    risk = float(entry_d - stop_d)
    if risk <= 0:
        raise SystemExit("stop arredondado ficou igual a entrada; o risco e menor que 1 tick")
    if args.target.lower().endswith("r"):
        rr = float(args.target[:-1].replace(",", "."))
        target_d = c.floor_step(float(entry_d) + rr * risk, tick)
    elif args.target.startswith("price:"):
        target_d = c.floor_step(float(args.target[6:]), tick)
    else:
        raise SystemExit("--target: 2R, 1.5R ou price:X")
    e, s, t = float(entry_d), float(stop_d), float(target_d)
    if t <= e:
        raise SystemExit("alvo precisa ficar acima da entrada")
    unit = SIM.r_unit(e, s, fee)
    net_rr = SIM.net_r(e, t, s, fee)
    iv_min = data.INTERVAL_MS[args.interval] // 60000
    horizon = max(1, int(cfg["max_hold_hours"] * 60 // iv_min))
    p = {"symbol": sym, "base": rules["base"], "quote": quote, "side": "BUY",
         "setup": sig["setup"], "stop_rule": sig.get("stop_rule"), "note": sig.get("note"),
         "signal_time": sig["t"], "interval": args.interval, "lookback": args.lookback,
         "vol_mult": args.vol_mult, "min_stop_pct": cfg["min_stop_pct"], "entry": c.dstr(entry_d), "stop": c.dstr(stop_d),
         "target": c.dstr(target_d), "risk_per_unit_net": unit, "net_reward_risk": net_rr,
         "gross_reward_risk": (t - e) / (e - s), "fee_pct": cfg["fee_pct"],
         "max_hold_hours": cfg["max_hold_hours"], "horizon_bars": horizon,
         "created": c.ms(c.now(args.now))}
    print("Plano: %s compra (spot), setup %s, velas de %s" % (sym, p["setup"], args.interval))
    print("  %s%s" % (p["note"] or "", "; stop alargado ao minimo de %s (mais perto disso e quase so taxa)" % c.pct(cfg["min_stop_pct"]) if sig.get("widened") else ""))
    print("  Entrada   %s  (ordem LIMIT)" % c.price_str(e, quote))
    print("  Stop      %s  (%s, %s abaixo)" % (c.price_str(s, quote), p["stop_rule"], c.pct((e - s) / e * 100, 2)))
    print("  Alvo      %s  (%s)" % (c.price_str(t, quote), args.target))
    print("  Taxas     %s por lado: risco real por unidade %s" % (c.pct(cfg["fee_pct"], 2), c.price_str(unit, quote)))
    print("  Ganho:risco  %.2f bruto, %.2f depois das taxas" % (p["gross_reward_risk"], net_rr))
    print("  Prazo     sair em ate %dh, ganhando ou perdendo" % cfg["max_hold_hours"])
    if net_rr < cfg["min_reward_risk"]:
        print("  ATENCAO: ganho:risco liquido %.2f abaixo do minimo %.2f da sua config: "
              "o verificador de risco vai bloquear." % (net_rr, cfg["min_reward_risk"]))
    if (e - s) / e < cfg["fee_pct"] / 100 * 4:
        print("  ATENCAO: stop muito curto perto das taxas: elas comem boa parte do ganho.")
    c.write_json(args.write or c.path("plan.json"), p)
    print("Gravado em %s. Nada foi enviado." % (args.write or c.path("plan.json")))
    print("Proximo: sim (maquina do tempo), depois size.")
    return p


# ---------------------------------------------------------------- sim
def run_sim(args, cfg):
    p = c.read_json(args.plan or c.path("plan.json"))
    if not p:
        raise SystemExit("sem plano: rode plan primeiro")
    quote = p["quote"]
    iv = p["interval"]
    iv_ms = data.INTERVAL_MS[iv]
    bars_needed = int(args.days * 86400000 // iv_ms)
    end = c.from_ms(p["signal_time"] + iv_ms)          # history up to the signal candle, inclusive
    hist = data.klines(p["symbol"], iv, bars_needed, offline=args.offline, end=end, now_ms=c.ms(end))
    if len(hist) < 200:
        raise SystemExit("historico curto demais (%d velas)" % len(hist))
    fee = p["fee_pct"] / 100
    e, s, t = float(p["entry"]), float(p["stop"]), float(p["target"])
    boot = SIM.bootstrap(hist, p["signal_time"], iv_ms, e, s, t, p["horizon_bars"], fee,
                         n=args.replays, seed=args.seed)
    hist_res = None
    if p["setup"] != "manual":
        rr = (t - e) / (e - s)
        hist_res = SIM.backtest(p["setup"], hist, rr, p["horizon_bars"], fee,
                                p.get("stop_rule"), p.get("lookback", 24), p.get("vol_mult", 1.5),
                                p.get("min_stop_pct", S.MIN_STOP_PCT))
    days_real = (hist[-1]["t"] - hist[0]["t"]) / 86400000
    print("Maquina do tempo: %s, entrada %s, stop %s, alvo %s, prazo %dh (%d velas de %s)." % (
        p["symbol"], c.price_str(e, quote), c.price_str(s, quote), c.price_str(t, quote),
        p["max_hold_hours"], p["horizon_bars"], iv))
    print("\n%d replays (semente %d), cada vela sorteada das velas reais do %s no mesmo horario do dia, "
          "ultimos %.0f dias:" % (boot["n"], boot["seed"], p["symbol"], days_real))
    print("  alvo primeiro   %s" % c.pct(boot["alvo"] * 100))
    print("  stop primeiro   %s" % c.pct(boot["stop"] * 100))
    print("  nem um nem outro %s  (sai no prazo, pelo preco do momento)" % c.pct(boot["tempo"] * 100))
    print("  terminou no lucro (depois das taxas) %s" % c.pct(boot["positive"] * 100))
    print("  resultado medio %+.3fR  (1R = a perda no stop, taxas incluidas)" % boot["avg_r"])
    print("  preco no fim: mediana %s, 90%% entre %s e %s" % (
        c.price_str(boot["p50"], quote), c.price_str(boot["p05"], quote), c.price_str(boot["p95"], quote)))
    if hist_res is not None:
        print("\nHistorico real: o mesmo setup com a mesma regra de stop e alvo nos ultimos %.0f dias:" % days_real)
        if hist_res["n"] == 0:
            print("  nao disparou nenhuma vez.")
        else:
            h = hist_res
            print("  %d sinais: %d alvo, %d stop, %d pelo prazo. Acerto %s. Media %+.3fR." % (
                h["n"], h["alvo"], h["stop"], h["tempo"], c.pct(h["win_rate"] * 100), h["avg_r"]))
            if h["avg_r_first_half"] is not None and h["n"] >= 6:
                print("  Estabilidade (walk-forward): 1a metade %+.3fR, 2a metade %+.3fR%s" % (
                    h["avg_r_first_half"], h["avg_r_second_half"],
                    "  <- mudou de sinal: a vantagem nao e estavel" if
                    h["avg_r_first_half"] * h["avg_r_second_half"] < 0 else ""))
    v = SIM.verdict(boot, hist_res)
    print("\n" + v)
    print("Simulacao, nao previsao: noticias, hacks e listagens fazem coisas que o historico nunca fez.")
    out = {"plan_fp": c.fingerprint({k: p[k] for k in ("symbol", "entry", "stop", "target")}),
           "bootstrap": boot, "history": {k: v2 for k, v2 in (hist_res or {}).items() if k != "trades"},
           "verdict": v}
    if args.write is not None:
        c.write_json(args.write or c.path("sim.json"), out)
        print("Gravado em %s." % (args.write or c.path("sim.json")))
    return out
