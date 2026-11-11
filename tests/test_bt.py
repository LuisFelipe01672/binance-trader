"""Offline tests: real Binance data saved in tests/fixtures, no network, no keys.

    python3 -m unittest discover -s tests -v
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "binance-trader", "scripts"))

import bt  # noqa: E402
from btlib import broker, common as c, data, ind as I, journal as J, risk, setups as S, sim as SIM, size  # noqa: E402

FIX = os.path.join(HERE, "fixtures")
BTC = os.path.join(FIX, "klines-BTCBRL-15m.json.gz")
BTC1D = os.path.join(FIX, "klines-BTCUSDT-1d.json.gz")
RULES = os.path.join(FIX, "exchangeInfo-BRL.json")


class Home(unittest.TestCase):
    """Each test gets an empty bt/ folder and no Binance keys."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.env = dict(os.environ)
        os.environ["BT_HOME"] = os.path.join(self.dir, "bt")
        for k in ("BINANCE_API_KEY", "BINANCE_SECRET_KEY", "BINANCE_API_SECRET", "BT_ALLOW_LIVE"):
            os.environ.pop(k, None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)
        shutil.rmtree(self.dir)

    def cfg(self, **kw):
        if kw:
            c.write_json(c.path("config.json"), kw)
        return c.load_config()

    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            bt.main(list(argv))
        return out.getvalue()


def bar(o, h, l, cl, t=0):
    return {"t": t, "o": o, "h": h, "l": l, "c": cl, "v": 1, "ct": t + 1, "qv": cl}


class TestBasics(unittest.TestCase):
    def test_signature_matches_binance_docs_example(self):
        # Example from Binance's spot API docs (SIGNED endpoint security, HMAC).
        secret = "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j"
        q = "symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1&recvWindow=5000&timestamp=1499827319559"
        self.assertEqual(broker.sign(q, secret), "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71")

    def test_yes_words(self):
        for r in ("sim", "Sim!", "SIM", "yes", "confirmo", "pode enviar", "s"):
            self.assertTrue(broker.is_yes(r), r)
        for r in ("", "nao", "não", "talvez", "sim?", "acho que sim", "no", "espera", "simsim", "ok"):
            self.assertFalse(broker.is_yes(r), r)

    def test_rounding(self):
        self.assertEqual(c.dstr(c.floor_step("0.0058312", "0.00001")), "0.00583")
        self.assertEqual(c.dstr(c.floor_step(429017.9, "1")), "429017")
        self.assertEqual(c.money(1234.5), "R$ 1.234,50")
        self.assertEqual(c.money(-7.981), "-R$ 7,98")

    def test_rsi_and_ema(self):
        up = [float(x) for x in range(1, 40)]
        self.assertEqual(I.rsi(up)[-1], 100.0)
        self.assertAlmostEqual(I.ema([2.0] * 30, 10)[-1], 2.0)
        r = I.rsi([10, 11, 10, 11, 10, 11, 10, 11, 10, 11, 10, 11, 10, 11, 10, 11.0])
        self.assertTrue(40 < r[-1] < 60)

    def test_klines_from_fixture(self):
        # saved at 08/11 12:00 Brasilia: the 12:00 candle was still forming, so it is left out
        b = data.klines("BTCBRL", "15m", 2880, offline=BTC, now_ms=1794150000000)
        self.assertEqual(len(b), 2879)
        self.assertTrue(all(b[k]["t"] - b[k - 1]["t"] == 900000 for k in range(1, len(b))))
        self.assertTrue(all(x["l"] <= min(x["o"], x["c"]) and x["h"] >= max(x["o"], x["c"]) for x in b))

    def test_rules(self):
        r = data.symbol_rules("BTCBRL", offline=RULES)
        self.assertEqual((r["tick"], r["step"]), ("1.00000000", "0.00001000"))
        self.assertIn("STOP_LOSS", r["order_types"])
        self.assertTrue(r["oto"] and r["oco"])


class TestSetupsAndSim(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars = data.klines("BTCBRL", "15m", 2880, offline=BTC)
        cls.ind = I.compute(cls.bars)

    def test_every_setup_fires_with_stop_below_entry_and_min_distance(self):
        for setup in S.SETUPS:
            sigs = [s for s in (S.detect(setup, self.bars, self.ind, i) for i in range(len(self.bars))) if s]
            self.assertGreater(len(sigs), 0, setup)
            for s in sigs:
                self.assertLess(s["stop"], s["entry"])
                self.assertGreaterEqual((s["entry"] - s["stop"]) / s["entry"], 0.006 - 1e-12)

    def test_breakout_is_a_first_close_above_the_range(self):
        for i in range(60, len(self.bars)):
            s = S.detect("rompimento", self.bars, self.ind, i)
            if s:
                self.assertGreater(self.bars[i]["c"], s["range_hi"])
                self.assertLessEqual(self.bars[i - 1]["c"], s["range_hi"])

    def test_walk_counts_stop_first_when_one_candle_touches_both(self):
        b = [bar(100, 112, 89, 100)]
        self.assertEqual(SIM.walk(b, 0, 100, 90, 110, 5)[0], "stop")

    def test_walk_gap_fills_at_open(self):
        out, px, _ = SIM.walk([bar(85, 86, 80, 82)], 0, 100, 90, 110, 5)
        self.assertEqual((out, px), ("stop", 85))

    def test_net_r_is_minus_one_at_the_stop(self):
        self.assertAlmostEqual(SIM.net_r(100, 98, 98, 0.001), -1.0)
        self.assertLess(SIM.net_r(100, 104, 98, 0.001), 2.0)   # fees shave the 2R target

    def test_bootstrap_is_repeatable_and_adds_up(self):
        t = self.bars[-1]["t"]
        a = SIM.bootstrap(self.bars, t, 900000, 429000, 426400, 434200, 48, 0.001, n=2000, seed=3)
        b = SIM.bootstrap(self.bars, t, 900000, 429000, 426400, 434200, 48, 0.001, n=2000, seed=3)
        self.assertEqual(a, b)
        self.assertAlmostEqual(a["alvo"] + a["stop"] + a["tempo"], 1.0)
        # stop 0.6% below, target 1.2% above: a random walk hits the stop first about 2/3 of the time
        self.assertTrue(0.55 < a["stop"] / (a["stop"] + a["alvo"]) < 0.78)

    def test_backtest_trades_do_not_overlap(self):
        h = SIM.backtest("rompimento", self.bars, 2.0, 48, 0.001)
        self.assertGreater(h["n"], 5)
        ts = h["trades"]
        for a, b in zip(ts, ts[1:]):
            self.assertGreater(b["t"], a["t"] + a["bars"] * 900000)


class TestPipeline(Home):
    def plan_and_size(self, **cfg):
        base = {"budget": 2000, "max_loss_per_trade": 20, "daily_loss_limit": 60, "min_reward_risk": 1.2}
        base.update(cfg)
        self.cfg(**base)
        self.run_cli("plan", "--symbol", "BTC", "--setup", "rompimento", "--recent", "400",
                     "--offline", BTC, "--rules", RULES)
        self.run_cli("regime", "--offline", BTC1D, "--write")
        self.run_cli("sim", "--offline", BTC, "--replays", "500", "--write")
        self.run_cli("size", "--rules", RULES)
        return c.read_json(c.path("orders.json"))["orders"][0]

    def test_full_flow_writes_a_valid_otoco(self):
        o = self.plan_and_size()
        p = o["params"]
        r = data.symbol_rules("BTCBRL", offline=RULES)
        self.assertEqual(p["workingSide"], "BUY")
        self.assertEqual(p["pendingSide"], "SELL")
        self.assertEqual(c.D(p["workingQuantity"]) % c.D(r["step"]), 0)
        self.assertEqual(c.D(p["pendingQuantity"]) % c.D(r["step"]), 0)
        self.assertLess(c.D(p["pendingQuantity"]), c.D(p["workingQuantity"]))     # fee paid in BTC
        self.assertLess(c.D(p["pendingBelowStopPrice"]), c.D(p["workingPrice"]))
        self.assertGreater(c.D(p["pendingAbovePrice"]), c.D(p["workingPrice"]))
        self.assertLessEqual(o["max_loss"], 20)
        self.assertGreaterEqual(float(p["workingQuantity"]) * float(p["workingPrice"]), 10)   # min notional

    def test_stop_limit_when_market_stop_not_allowed(self):
        p = c.read_json(os.path.join(FIX, "..", "fixtures", "exchangeInfo-BRL.json"))
        rules = data.symbol_rules("BTCBRL", offline=RULES)
        rules["order_types"] = [t for t in rules["order_types"] if t != "STOP_LOSS"]
        plan = {"symbol": "BTCBRL", "entry": "429017", "stop": "426442", "target": "434167"}
        params, _ = size.build_otoco(plan, c.D("0.002"), rules, c.load_config())
        self.assertEqual(params["pendingBelowType"], "STOP_LOSS_LIMIT")
        self.assertEqual(params["pendingBelowPrice"], "425162")     # 0.3% under the stop, tick 1
        self.assertIsNotNone(p)

    def test_fees_in_bnb_sell_the_full_quantity(self):
        rules = data.symbol_rules("BTCBRL", offline=RULES)
        plan = {"symbol": "BTCBRL", "entry": "429017", "stop": "426442", "target": "434167"}
        cfg = dict(c.load_config(), fee_in_bnb=True)
        params, sell = size.build_otoco(plan, c.D("0.00233"), rules, cfg)
        self.assertEqual(params["pendingQuantity"], "0.00233")

    def test_tiny_budget_is_refused_not_rounded_to_zero(self):
        self.cfg(budget=15, max_loss_per_trade=0.01)
        self.run_cli("plan", "--symbol", "BTC", "--setup", "rompimento", "--recent", "400", "--offline", BTC, "--rules", RULES)
        out = self.run_cli("size", "--rules", RULES)
        self.assertIn("NAO DA", out)
        self.assertEqual(c.read_json(c.path("orders.json"))["orders"], [])

    def test_risk_pass_then_blocks(self):
        o = self.plan_and_size()
        cfg = c.load_config()
        now = c.now("2026-11-08 12:00")
        st = risk.account_state(cfg, now)
        status, why, warn = risk.check_order(o, cfg, st, now, {"time": c.ms(now), "posture": "neutro"}, None, None)
        self.assertEqual(status, "PASS", why)
        # risk-off market blocks a new long
        status, why, _ = risk.check_order(o, cfg, st, now, {"time": c.ms(now), "posture": "risco-desligado"}, None, None)
        self.assertEqual(status, "BLOCK")
        # price already at the stop
        status, why, _ = risk.check_order(o, cfg, st, now, None, None, float(o["stop"]) - 1)
        self.assertEqual(status, "BLOCK")
        # running away from the entry is a warning, not a block
        status, why, warn = risk.check_order(o, cfg, st, now, None, None, float(o["entry"]) * 1.01)
        self.assertEqual(status, "PASS")
        self.assertTrue(any("nao corra" in w for w in warn))

    def test_circuit_breakers(self):
        o = self.plan_and_size()
        cfg = c.load_config()
        now = c.now("2026-11-08 15:00")
        doc = {"trades": []}
        for k, minutes_ago in enumerate((200, 100, 10)):
            t = c.ms(now) - minutes_ago * 60000
            doc["trades"].append({"id": k + 1, "symbol": "BTCBRL", "base": "BTC", "quote": "BRL", "env": "testnet",
                                  "opened": t - 60000, "closed": t, "qty": "0.001", "sell_qty": "0.001",
                                  "entry": "400000", "stop": "398000", "target": "404000", "max_loss": 5,
                                  "pnl": -5.0, "r": -1.0})
        J.save(doc)
        st = risk.account_state(cfg, now)
        self.assertEqual((st["opened_today"], st["streak"]), (3, 3))
        status, why, _ = risk.check_order(o, cfg, st, now, None, None, None)
        self.assertEqual(status, "BLOCK")
        text = " | ".join(why)
        self.assertIn("ja foram 3 trades", text)
        self.assertIn("perdas seguidas", text)
        self.assertIn("perda ha 10 min", text)

    def test_daily_limit_counts_open_risk(self):
        o = self.plan_and_size(daily_loss_limit=10)
        cfg = c.load_config()
        now = c.now("2026-11-08 15:00")
        J.save({"trades": [{"id": 1, "symbol": "ETHBRL", "base": "ETH", "quote": "BRL", "env": "testnet",
                            "opened": c.ms(now) - 60000, "qty": "0.01", "sell_qty": "0.01", "entry": "12000",
                            "stop": "11900", "target": "12200", "max_loss": 4}]})
        st = risk.account_state(cfg, now)
        status, why, _ = risk.check_order(o, cfg, st, now, None, None, None)
        self.assertEqual(status, "BLOCK")
        self.assertIn("limite diario", " ".join(why))


class FakeBinance:
    """Records requests; answers like Binance's spot API."""

    def __init__(self):
        self.calls = []

    def __call__(self, method, url, params=None, headers=None, **kw):
        self.calls.append((method, url, dict(params or {}), dict(headers or {})))
        if url.endswith("/api/v3/time"):
            return {"serverTime": 1794150000000}
        if url.endswith("/api/v3/orderList/otoco"):
            return {"orderListId": 4242, "listOrderStatus": "EXECUTING"}
        if url.endswith("/api/v3/order"):
            return {"orderId": 77, "status": "FILLED"}
        raise AssertionError("unexpected call " + url)


class TestBroker(Home):
    NOW = "2026-11-08 12:00"

    def ready_order(self, **cfg):
        base = {"budget": 2000, "max_loss_per_trade": 20, "daily_loss_limit": 60, "min_reward_risk": 1.2}
        base.update(cfg)
        self.cfg(**base)
        self.run_cli("plan", "--symbol", "BTC", "--setup", "rompimento", "--recent", "400", "--offline", BTC, "--rules", RULES)
        self.run_cli("regime", "--offline", BTC1D, "--write", "--now", self.NOW)
        self.run_cli("size", "--rules", RULES)
        out = self.run_cli("risk", "--no-price", "--now", self.NOW)
        self.assertIn("PASS  #1", out)
        os.environ["BINANCE_API_KEY"] = "test-key"
        os.environ["BINANCE_SECRET_KEY"] = "test-secret"
        return c.load_config()

    def args(self, **kw):
        a = type("A", (), {})()
        a.now, a.index, a.live, a.dry_run, a.reply = self.NOW, 1, False, False, "sim"
        for k, v in kw.items():
            setattr(a, k, v)
        return a

    def test_no_order_without_a_yes(self):
        cfg = self.ready_order()
        fake = FakeBinance()
        with self.assertRaises(SystemExit) as e:
            broker.place(self.args(), cfg, http=fake)
        self.assertIn("falta o seu sim", str(e.exception))
        self.assertEqual(fake.calls, [])

    def test_ambiguous_reply_is_not_a_yes(self):
        cfg = self.ready_order()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(broker.approve(self.args(reply="acho que sim"), cfg))

    def test_no_order_without_risk_pass(self):
        cfg = self.ready_order()
        c.write_json(c.path("state", "risk.json"), {})
        with self.assertRaises(SystemExit):
            broker.place(self.args(), cfg, http=FakeBinance())

    def test_yes_expires(self):
        cfg = self.ready_order()
        with contextlib.redirect_stdout(io.StringIO()):
            broker.approve(self.args(), cfg)
        with self.assertRaises(SystemExit) as e:
            broker.place(self.args(now="2026-11-08 12:30"), cfg, http=FakeBinance())
        self.assertIn("mais de 15 min", str(e.exception))

    def test_places_signed_otoco_on_testnet_once(self):
        cfg = self.ready_order()
        fake = FakeBinance()
        with contextlib.redirect_stdout(io.StringIO()):
            broker.approve(self.args(), cfg)
            broker.place(self.args(), cfg, http=fake)
        method, url, params, headers = fake.calls[-1]
        self.assertEqual((method, url), ("POST", "https://testnet.binance.vision/api/v3/orderList/otoco"))
        self.assertEqual(headers["X-MBX-APIKEY"], "test-key")
        sig = params.pop("signature")
        self.assertEqual(sig, broker.sign(urllib.parse.urlencode(params), "test-secret"))
        self.assertEqual(params["timestamp"], 1794150000000)
        self.assertNotIn("test-secret", json.dumps(fake.calls))
        trades = J.load()["trades"]
        self.assertEqual((len(trades), trades[0]["broker_ref"], trades[0]["env"]), (1, 4242, "testnet"))
        with self.assertRaises(SystemExit) as e:            # one yes = one order
            broker.place(self.args(), cfg, http=fake)
        self.assertIn("ja foi enviada", str(e.exception))

    def test_real_money_needs_three_locks(self):
        cfg = self.ready_order(env="prod")
        fake = FakeBinance()
        with contextlib.redirect_stdout(io.StringIO()):
            broker.approve(self.args(), cfg)
        for live, allow, envvar in ((False, True, "1"), (True, False, "1"), (True, True, None)):
            cfg["allow_live"] = allow
            os.environ.pop("BT_ALLOW_LIVE", None)
            if envvar:
                os.environ["BT_ALLOW_LIVE"] = envvar
            with self.assertRaises(SystemExit) as e:
                broker.place(self.args(live=live), cfg, http=fake)
            self.assertIn("tres travas", str(e.exception))
        self.assertEqual(fake.calls, [])
        cfg["allow_live"] = True
        os.environ["BT_ALLOW_LIVE"] = "1"
        with contextlib.redirect_stdout(io.StringIO()):
            broker.place(self.args(live=True), cfg, http=fake)
        self.assertEqual(fake.calls[-1][1], "https://api.binance.com/api/v3/orderList/otoco")

    def test_missing_keys_stop_before_any_request(self):
        cfg = self.ready_order()
        os.environ.pop("BINANCE_API_KEY")
        fake = FakeBinance()
        with contextlib.redirect_stdout(io.StringIO()):
            broker.approve(self.args(), cfg)
        with self.assertRaises(SystemExit) as e:
            broker.place(self.args(), cfg, http=fake)
        self.assertIn("BINANCE_API_KEY", str(e.exception))
        self.assertEqual(fake.calls, [])

    def test_ticket_shows_cli_equivalent(self):
        self.ready_order()
        out = self.run_cli("ticket", "--now", self.NOW)
        self.assertIn("PRONTA", out)
        self.assertIn("binance-cli spot order-list-otoco --symbol BTCBRL --working-type LIMIT", out)


class TestJournal(Home):
    def test_close_and_csv(self):
        cfg = self.cfg()
        now = c.now("2026-11-08 10:00")
        o = {"symbol": "BTCBRL", "base": "BTC", "quote": "BRL", "qty": "0.002", "sell_qty": "0.001998",
             "entry": "400000", "stop": "396000", "target": "408000", "max_loss": 9.6}
        tid = J.open_trade(o, now, "testnet")
        a = type("A", (), {"id": tid, "exit": 408000.0, "entry_fill": None, "fees": None, "reason": "alvo",
                           "now": "2026-11-08 13:00", "out": None})()
        with contextlib.redirect_stdout(io.StringIO()):
            J.close_trade(a, cfg)
            J.export_csv(a, cfg)
        t = J.load()["trades"][0]
        self.assertAlmostEqual(t["fees"], (800 + 0.001998 * 408000) * 0.001)
        self.assertAlmostEqual(t["pnl"], 0.001998 * 408000 - 800 - t["fees"])
        with open(c.path("diario.csv"), encoding="utf-8") as f:
            rows = f.read().splitlines()
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[1].startswith("1;testnet;08/11/2026 10:00;08/11/2026 13:00;BTCBRL"))


if __name__ == "__main__":
    unittest.main()
