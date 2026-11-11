#!/usr/bin/env python3
"""binance-trader: day trade de cripto na Binance (spot, so compra), com freios.

  regime   clima do mercado (BTC diario)          scan     procura setups nos pares em BRL
  plan     entrada, stop e alvo                   sim      maquina do tempo (10.000 replays + historico)
  size     tamanho pela perda maxima -> OTOCO     risk     PASS / BLOCK pelas suas regras
  ticket   mostra o boleto                        approve  registra o seu "sim"
  place    envia (testnet por padrao)             status   saldos e ordens abertas
  cancel   cancela ordens abertas de um par       close    monta venda a mercado para sair
  price    preco agora                            journal  diario: show | close | csv

Tudo e gravado em ./bt (ou BT_HOME). Padrao: testnet, dinheiro de mentira.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from btlib import broker, common as c, data, journal, market, risk, size  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bt.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="config (padrao bt/config.json)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, **kw):
        p = sub.add_parser(name, **kw)
        p.add_argument("--now", help='fingir que agora e "AAAA-MM-DD HH:MM" (horario de Brasilia)')
        return p

    p = add("regime", help="clima do mercado")
    p.add_argument("--offline", help="klines BTCUSDT 1d salvos")
    p.add_argument("--write", nargs="?", const="", default=None)

    p = add("scan", help="procura setups")
    p.add_argument("--setup", help="rompimento,pullback,repique (padrao: todos)")
    p.add_argument("--symbols", help="ex.: BTC,ETH,SOL (padrao: todos os pares em BRL)")
    p.add_argument("--interval", default="15m")
    p.add_argument("--lookback", type=int, default=24, help="velas da faixa do rompimento")
    p.add_argument("--vol-mult", type=float, default=1.5)
    p.add_argument("--recent", type=int, default=1, help="olhar as ultimas N velas fechadas")
    p.add_argument("--min-volume", type=float, help="volume minimo 24h (padrao: min_volume_24h da config)")
    p.add_argument("--offline-dir", help=argparse.SUPPRESS)

    p = add("plan", help="entrada, stop e alvo")
    p.add_argument("--symbol", required=True)
    p.add_argument("--setup")
    p.add_argument("--entry", type=float, help="entrada manual")
    p.add_argument("--stop", help="range-mid, range-low, swing-low, atr:1.5, pct:2, price:X")
    p.add_argument("--target", default="2R", help="2R, 1.5R ou price:X")
    p.add_argument("--interval", default="15m")
    p.add_argument("--lookback", type=int, default=24)
    p.add_argument("--vol-mult", type=float, default=1.5)
    p.add_argument("--recent", type=int, default=3)
    p.add_argument("--offline")
    p.add_argument("--rules", help=argparse.SUPPRESS)
    p.add_argument("--write")

    p = add("sim", help="maquina do tempo")
    p.add_argument("--plan")
    p.add_argument("--days", type=float, default=30)
    p.add_argument("--replays", type=int, default=10000)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--offline")
    p.add_argument("--write", nargs="?", const="", default=None)

    p = add("size", help="tamanho da posicao e ordem OTOCO")
    p.add_argument("--plan")
    p.add_argument("--budget", type=float)
    p.add_argument("--rules", help=argparse.SUPPRESS)
    p.add_argument("--write")

    p = add("risk", help="PASS / BLOCK")
    p.add_argument("--orders")
    p.add_argument("--price", type=float, help="usar este preco em vez do ao vivo")
    p.add_argument("--no-price", action="store_true", help="nao buscar o preco ao vivo")

    add("ticket", help="mostra o boleto")

    p = add("approve", help='registra o "sim"')
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--reply", required=True, help="a resposta do usuario, palavra por palavra")

    p = add("place", help="envia a ordem aprovada")
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--live", action="store_true", help="terceira trava para conta real")
    p.add_argument("--dry-run", action="store_true")

    add("status", help="saldos e ordens abertas")

    p = add("cancel", help="cancela as ordens abertas de um par")
    p.add_argument("--symbol", required=True)
    p.add_argument("--reply", required=True)

    p = add("close", help="monta uma venda a mercado para sair")
    p.add_argument("--symbol", required=True)
    p.add_argument("--qty", type=float, required=True)
    p.add_argument("--rules", help=argparse.SUPPRESS)

    p = add("price", help="preco agora")
    p.add_argument("symbols", nargs="+")

    p = add("journal", help="diario de trades")
    p.add_argument("action", choices=["show", "close", "csv"])
    p.add_argument("--id", type=int)
    p.add_argument("--exit", type=float)
    p.add_argument("--entry-fill", type=float)
    p.add_argument("--fees", type=float)
    p.add_argument("--reason")
    p.add_argument("--last", type=int, default=20)
    p.add_argument("--out")

    args = ap.parse_args(argv)
    cfg = c.load_config(args.config)
    try:
        if args.cmd == "regime":
            market.regime(args, cfg)
        elif args.cmd == "scan":
            market.scan(args, cfg)
        elif args.cmd == "plan":
            market.plan(args, cfg)
        elif args.cmd == "sim":
            market.run_sim(args, cfg)
        elif args.cmd == "size":
            size.size(args, cfg)
        elif args.cmd == "risk":
            risk.run(args, cfg)
        elif args.cmd == "ticket":
            broker.show(args, cfg)
        elif args.cmd == "approve":
            broker.approve(args, cfg)
        elif args.cmd == "place":
            broker.place(args, cfg)
        elif args.cmd == "status":
            broker.status(args, cfg)
        elif args.cmd == "cancel":
            broker.cancel(args, cfg)
        elif args.cmd == "close":
            size.close_order(args, cfg)
        elif args.cmd == "price":
            for s in args.symbols:
                s = s.upper() if s.upper().endswith(cfg["quote"]) else s.upper() + cfg["quote"]
                print("%s  %s" % (s, c.price_str(data.price(s), cfg["quote"])))
        elif args.cmd == "journal":
            if args.action == "show":
                journal.show(args, cfg)
            elif args.action == "close":
                if args.id is None or args.exit is None:
                    raise SystemExit("journal close precisa de --id e --exit")
                journal.close_trade(args, cfg)
            else:
                journal.export_csv(args, cfg)
    except c.HttpError as e:
        raise SystemExit("erro da Binance: %s" % e)


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:          # output piped to head/less that closed early
        sys.stderr.close()
