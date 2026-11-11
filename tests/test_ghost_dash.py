"""Ghost trades, dashboard and daily summary."""
import os
import sys
import unittest
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "binance-trader", "scripts"))

from test_bt import BTC, Home, bar  # noqa: E402
from test_telegram_bot import NOW, TestBot as BotBase  # noqa: E402
import telegram_bot as TB  # noqa: E402
from btlib import common as c, dash, data, ghost as G, journal as J  # noqa: E402

IV = 900000
T0 = 1794150000000


def g0(**kw):
    g = {"key": "k", "symbol": "BTCBRL", "quote": "BRL", "setup": "rompimento", "interval": "15m",
         "signal_time": T0, "entry": 100.0, "stop": 98.0, "target": 104.0, "fee_pct": 0.1,
         "horizon_bars": 4, "entry_timeout_bars": 2, "decision": "bloqueado", "reason": "x", "status": G.OPEN}
    g.update(kw)
    return g


def bars(*ohlc):
    return [dict(bar(*x, t=T0 + IV * (k + 1)), ct=T0 + IV * (k + 2) - 1) for k, x in enumerate(ohlc)]


class TestGhostResolve(unittest.TestCase):
    def test_target(self):
        g = G.resolve(g0(), bars((100.5, 101, 99.9, 100.5), (100.5, 104.5, 100.2, 104)))
        self.assertEqual(g["status"], "alvo")
        self.assertAlmostEqual(g["r"], (104 * 0.999 - 100 * 1.001) / (100 * 1.001 - 98 * 0.999))

    def test_stop_in_the_fill_candle(self):
        g = G.resolve(g0(), bars((100.5, 101, 97.5, 98)))
        self.assertEqual(g["status"], "stop")
        self.assertAlmostEqual(g["r"], -1.0)

    def test_limit_never_filled(self):
        g = G.resolve(g0(), bars((100.5, 101, 100.1, 100.8)))
        self.assertEqual(g["status"], G.OPEN)                       # timeout not reached yet
        g = G.resolve(g0(), bars((100.5, 101, 100.1, 100.8), (101, 102, 100.4, 101.5)))
        self.assertEqual((g["status"], g["r"]), ("nao executou", 0.0))

    def test_still_running_then_time_exit(self):
        b = [(100.2, 100.5, 99.8, 100.1), (100.1, 101, 99.5, 100.6)]
        g = G.resolve(g0(), bars(*b))
        self.assertEqual(g["status"], G.OPEN)
        b += [(100.6, 101, 100, 100.9)] * 3
        g = G.resolve(g0(), bars(*b))
        self.assertEqual(g["status"], "tempo")
        self.assertAlmostEqual(g["exit"], 100.9)

    def test_stats_by_group(self):
        gs = [g0(status="alvo", r=1.5, decision="bloqueado"), g0(status="stop", r=-1.0, decision="recusado"),
              g0(status="nao executou", r=0.0), g0()]
        s = G.stats(gs)
        self.assertEqual((s["total"], s["n"], s["open"], s["unfilled"]), (4, 2, 1, 1))
        self.assertAlmostEqual(s["avg_r"], 0.25)
        self.assertEqual(s["groups"][("decisao", "recusado por voce")]["n"], 1)

    def test_update_all_with_real_candles(self):
        home = os.environ.get("BT_HOME")
        import tempfile
        d = tempfile.mkdtemp()
        os.environ["BT_HOME"] = d
        try:
            real = data.klines("BTCBRL", "15m", 2880, offline=BTC)
            sig = real[-200]
            G.save({"ghosts": [g0(symbol="BTCBRL", signal_time=sig["t"], entry=sig["c"], stop=sig["c"] * 0.994,
                                  target=sig["c"] * 1.012, horizon_bars=48, entry_timeout_bars=4)]})
            changed = G.update_all(c.from_ms(real[-1]["t"]), fetch=lambda s, iv, n: real)
            self.assertEqual(len(changed), 1)
            self.assertNotEqual(G.load()["ghosts"][0]["status"], G.OPEN)
        finally:
            os.environ["BT_HOME"] = home or ""


class TestBotGhosts(BotBase):
    def test_every_decision_becomes_a_ghost(self):
        m = self.proposal()
        gh = G.load()["ghosts"]
        self.assertEqual(len(gh), 1)
        self.assertEqual(gh[0]["decision"], "proposto")
        self.press(m["buttons"][0][1][1], m["id"])                    # declined
        self.assertEqual(G.load()["ghosts"][0]["decision"], "recusado")

    def test_blocked_signal_is_still_followed(self):
        self.pair()
        self.cfg(budget=2000, max_loss_per_trade=20, daily_loss_limit=60, min_reward_risk=9, bot_skip_no_edge=False)
        self.bot.cfg = c.load_config()
        res = self.bot.evaluate("BTCBRL", "rompimento")
        self.assertIn("ganho:risco", res)
        g = G.load()["ghosts"][0]
        self.assertEqual(g["decision"], "bloqueado")
        self.assertIn("ganho:risco", g["reason"])

    def test_expired_proposal_is_marked(self):
        self.proposal()
        self.later(20)
        self.bot.live_pending()
        self.assertEqual(G.load()["ghosts"][0]["decision"], "expirou")

    def test_daily_summary_once_a_day(self):
        self.pair()
        self.bot.st["last_monitor"] = self.bot.st["last_regime"] = c.ms(self.clock[0])
        self.bot.st["last_ghosts"] = self.bot.st["last_scan"] = c.ms(self.clock[0])
        self.clock[0] = c.now("2026-11-08 21:05")
        self.bot.st["last_scan"] = c.ms(self.clock[0])
        self.bot.st["last_monitor"] = self.bot.st["last_ghosts"] = c.ms(self.clock[0])
        self.bot.st["last_regime"] = c.ms(self.clock[0])
        n = len(self.tg.sent)
        self.bot.tick()
        self.assertEqual(len(self.tg.sent), n + 1)
        self.assertIn("Resumo de 08/11", self.tg.sent[-1]["text"])
        self.bot.tick()
        self.assertEqual(len(self.tg.sent), n + 1)

    def test_export_sends_a_frozen_html_file(self):
        self.pair()
        self.msg("/exportar", uid=8)
        chat, name, content, caption = self.tg.docs[-1]
        self.assertEqual(chat, 555)
        self.assertTrue(name.startswith("painel-2026-11-08") and name.endswith(".html"))
        page = content.decode()
        self.assertIn("Painel do bot", page)
        self.assertNotIn('http-equiv="refresh"', page)

    def test_painel_command(self):
        self.pair()
        self.msg("/painel", uid=7)
        self.assertIn("http://localhost:8765", self.tg.sent[-1]["text"])


class TestDashboard(Home):
    def fill(self):
        cfg = self.cfg(quote="BRL")
        now = c.now("2026-11-08 20:00")
        o = {"symbol": "BTCBRL", "base": "BTC", "quote": "BRL", "qty": "0.002", "sell_qty": "0.001998",
             "entry": "400000", "stop": "396000", "target": "408000", "max_loss": 9.6}
        for k, px in enumerate((408000, 396000, 408000)):
            tid = J.open_trade(o, c.now("2026-11-08 1%d:00" % k), "testnet")
            J.finalize(tid, px, cfg, c.now("2026-11-08 1%d:30" % k))
        J.open_trade(o, now, "testnet")
        G.save({"ghosts": [g0(status="alvo", r=1.4, resolved=T0, id=1),
                           g0(status="stop", r=-1.0, resolved=T0 + 1, id=2, decision="recusado"), g0(id=3)]})
        c.write_json(c.path("regime.json"), {"time": c.ms(now), "posture": "risco-desligado", "score": -2, "btc": 73796,
                                             "ema50": 1, "ema200": 1, "change_7d": -4.7, "change_30d": -11.9,
                                             "avg_daily_move": 1.5})
        c.write_json(c.path("scan.json"), {"time": c.ms(now), "interval": "15m", "symbols": ["BTCBRL", "ETHBRL"], "hits": []})
        return cfg, now

    def test_page_has_every_section_and_no_external_resources(self):
        cfg, now = self.fill()
        page = dash.render(cfg, {"paused": False}, now, price=lambda s: 401000.0)
        for part in ("Clima do mercado", "RISCO-DESLIGADO", "Seus trades", "<svg", "Abertos agora",
                     "Trades fantasmas", "Ultimos sinais", "recusado por voce", "Moedas vigiadas", "ETH"):
            self.assertIn(part, page)
        self.assertNotIn("http://", page.replace("http-equiv", ""))
        self.assertNotIn("https://", page)

    def test_empty_page_renders(self):
        page = dash.render(self.cfg(), {}, c.now(NOW), price=lambda s: 1.0)
        self.assertIn("Nenhum trade fechado ainda", page)

    def test_summary(self):
        cfg, now = self.fill()
        txt = dash.summary_text(cfg, now)
        self.assertIn("Clima: RISCO-DESLIGADO", txt)
        self.assertIn("Fantasmas desde o inicio: 2", txt)

    def test_server_serves_the_page_on_localhost(self):
        cfg, now = self.fill()
        bot = type("B", (), {"st": {"paused": False}})()
        srv = TB.start_dashboard(bot, 0)
        try:
            port = srv.server_address[1]
            self.assertEqual(srv.server_address[0], "127.0.0.1")
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            body = opener.open("http://127.0.0.1:%d/" % port, timeout=30).read().decode()
            self.assertIn("Painel do bot", body)
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
