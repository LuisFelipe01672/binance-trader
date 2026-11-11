"""The Telegram bot end to end, with a fake Telegram and a fake Binance (no network)."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "binance-trader", "scripts"))

from test_bt import BTC, BTC1D, RULES, Home  # noqa: E402
import telegram_bot as TB  # noqa: E402
from btlib import common as c, journal as J  # noqa: E402

OWNER = 555
NOW = "2026-11-08 12:00"


class FakeTelegram:
    def __init__(self):
        self.sent, self.edits, self.answers, self.next_id = [], [], [], 100

    def send(self, chat, text, buttons=None):
        self.next_id += 1
        self.sent.append({"chat": chat, "text": text, "buttons": buttons, "id": self.next_id})
        return {"message_id": self.next_id}

    def edit(self, chat, mid, text):
        self.edits.append((chat, mid, text))

    def answer(self, cb_id, text=""):
        self.answers.append(text)


class FakeBinance:
    """Spot API stand-in. `fills` sets what each order looks like when queried."""

    def __init__(self):
        self.calls, self.status = [], {}

    def __call__(self, method, url, params=None, headers=None, **kw):
        ep = url.split(".vision")[-1].split(".com")[-1]
        self.calls.append((method, ep, dict(params or {})))
        p = params or {}
        if ep == "/api/v3/time":
            return {"serverTime": 1794150000000}
        if ep == "/api/v3/orderList/otoco" and method == "POST":
            self.otoco = p
            return {"orderListId": 9, "listOrderStatus": "EXECUTING"}
        if ep == "/api/v3/orderList" and method == "GET":
            return {"orderListId": 9, "orders": [{"symbol": "BTCBRL", "orderId": k} for k in (1, 2, 3)]}
        if ep == "/api/v3/orderList" and method == "DELETE":
            return {"orderListId": 9, "listOrderStatus": "ALL_DONE"}
        if ep == "/api/v3/order" and method == "GET":
            return self.status[p["orderId"]]
        if ep == "/api/v3/order" and method == "POST":
            q = float(p["quantity"])
            return {"orderId": 77, "status": "FILLED", "executedQty": str(q), "cummulativeQuoteQty": str(q * 430000)}
        raise AssertionError("unexpected %s %s" % (method, ep))

    def set(self, buy, tp, sl, buy_px=None, tp_px=None, sl_px=None, qty="0.00233", sqty="0.00232"):
        def o(oid, side, typ, st, px, q):
            done = st == "FILLED"
            return {"orderId": oid, "side": side, "type": typ, "status": st,
                    "executedQty": q if done else "0", "cummulativeQuoteQty": str(float(q) * px) if done else "0"}
        self.status = {1: o(1, "BUY", "LIMIT", buy, buy_px or 0, qty),
                       2: o(2, "SELL", "LIMIT_MAKER", tp, tp_px or 0, sqty),
                       3: o(3, "SELL", "STOP_LOSS", sl, sl_px or 0, sqty)}

    def sent(self, ep, method="POST"):
        return [x for x in self.calls if x[1] == ep and x[0] == method]


class TestBot(Home):
    def setUp(self):
        super().setUp()
        self.cfg(budget=2000, max_loss_per_trade=20, daily_loss_limit=60, min_reward_risk=1.2,
                 bot_skip_no_edge=False)
        os.environ["BINANCE_API_KEY"], os.environ["BINANCE_SECRET_KEY"] = "k", "s"
        self.clock = [c.now(NOW)]
        self.tg, self.bn = FakeTelegram(), FakeBinance()
        extra = {"plan": ["--offline", BTC, "--rules", RULES, "--recent", "400"],
                 "sim": ["--offline", BTC, "--replays", "300"], "size": ["--rules", RULES],
                 "risk": ["--no-price", "--now", NOW], "regime": ["--offline", BTC1D, "--now", NOW],
                 "rules": [RULES]}
        self.bot = TB.Bot(self.tg, c.load_config(), binance_http=self.bn, extra=extra,
                          clock=lambda: self.clock[0], log=lambda m: None)

    def later(self, minutes):
        self.clock[0] = c.from_ms(c.ms(self.clock[0]) + minutes * 60000)

    def msg(self, text, chat=OWNER, uid=1):
        self.bot.handle({"update_id": uid, "message": {"chat": {"id": chat}, "text": text}})

    def press(self, data, mid, chat=OWNER, uid=2):
        self.bot.handle({"update_id": uid, "callback_query": {"id": "cb", "data": data, "from": {"id": chat},
                                                              "message": {"message_id": mid, "chat": {"id": chat}}}})

    def pair(self):
        code = self.bot.pair_code()
        self.msg("/start " + code)
        self.assertEqual(self.bot.st["chat_id"], OWNER)

    def proposal(self):
        self.pair()
        self.assertIs(self.bot.evaluate("BTCBRL", "rompimento"), True)
        m = self.tg.sent[-1]
        self.assertTrue(m["buttons"])
        return m

    # ------------------------------------------------------------------
    def test_pairing_needs_the_code_and_ignores_strangers(self):
        code = self.bot.pair_code()
        self.msg("/start 000000" if code != "000000" else "/start 111111")
        self.assertIsNone(self.bot.st["chat_id"])
        self.msg("/start " + code)
        self.assertEqual(self.bot.st["chat_id"], OWNER)
        n = len(self.tg.sent)
        self.msg("/status", chat=999)
        self.assertEqual(len(self.tg.sent), n)                    # stranger gets nothing

    def test_proposal_shows_the_ticket_and_sends_nothing(self):
        m = self.proposal()
        self.assertIn("COMPRAR 0.00233 BTC", m["text"])
        self.assertIn("STOP", m["text"])
        self.assertIn("Maquina do tempo", m["text"])
        self.assertEqual(m["buttons"][0][0][1][:3], "ok:")
        self.assertEqual(self.bn.calls, [])

    def test_stranger_cannot_press_approve(self):
        m = self.proposal()
        self.press(m["buttons"][0][0][1], m["id"], chat=999)
        self.assertEqual(self.bn.sent("/api/v3/orderList/otoco"), [])
        self.assertIn("Nao autorizado.", self.tg.answers)

    def test_decline_sends_nothing(self):
        m = self.proposal()
        self.press(m["buttons"][0][1][1], m["id"])
        self.assertEqual(self.bn.calls, [])
        self.assertIn("RECUSADO", self.tg.edits[-1][2])

    def test_expired_proposal_sends_nothing(self):
        m = self.proposal()
        self.later(20)
        self.press(m["buttons"][0][0][1], m["id"])
        self.assertEqual(self.bn.sent("/api/v3/orderList/otoco"), [])

    def test_approve_places_then_follows_to_the_target(self):
        m = self.proposal()
        self.later(2)
        self.press(m["buttons"][0][0][1], m["id"])
        sent = self.bn.sent("/api/v3/orderList/otoco")
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][2]["workingSide"], "BUY")
        self.assertIn("ENVIADO", self.tg.edits[-1][2])
        # pressing again does not send twice
        self.press(m["buttons"][0][0][1], m["id"], uid=3)
        self.assertEqual(len(self.bn.sent("/api/v3/orderList/otoco")), 1)
        t = J.load()["trades"][0]
        # bought
        self.bn.set("FILLED", "NEW", "NEW", buy_px=float(t["entry"]))
        self.later(5)
        self.bot.monitor()
        self.assertIn("COMPROU", self.tg.sent[-1]["text"])
        # target filled
        self.bn.set("FILLED", "FILLED", "EXPIRED", buy_px=float(t["entry"]), tp_px=float(t["target"]))
        self.later(60)
        self.bot.monitor()
        t = J.load()["trades"][0]
        self.assertEqual(t["reason"], "alvo")
        self.assertGreater(t["pnl"], 0)
        self.assertIn("BATEU O ALVO", self.tg.sent[-1]["text"])

    def test_stop_hit_reports_a_loss(self):
        m = self.proposal()
        self.press(m["buttons"][0][0][1], m["id"])
        t = J.load()["trades"][0]
        self.bn.set("FILLED", "EXPIRED", "FILLED", buy_px=float(t["entry"]), sl_px=float(t["stop"]))
        self.later(30)
        self.bot.monitor()
        t = J.load()["trades"][0]
        self.assertEqual(t["reason"], "stop")
        self.assertLess(t["pnl"], 0)
        self.assertGreaterEqual(t["pnl"], -20.5)                 # about the planned max loss
        self.assertIn("SAIU NO STOP", self.tg.sent[-1]["text"])

    def test_entry_not_filled_is_cancelled_after_timeout(self):
        m = self.proposal()
        self.press(m["buttons"][0][0][1], m["id"])
        self.bn.set("NEW", "PENDING_NEW", "PENDING_NEW")
        self.later(30)
        self.bot.monitor()
        self.assertEqual(self.bn.sent("/api/v3/orderList", "DELETE"), [])
        self.later(40)
        self.bot.monitor()
        self.assertEqual(len(self.bn.sent("/api/v3/orderList", "DELETE")), 1)
        t = J.load()["trades"][0]
        self.assertTrue(t["unfilled"])
        self.assertEqual(t["pnl"], 0)

    def test_deadline_asks_and_exit_sells_at_market(self):
        m = self.proposal()
        self.press(m["buttons"][0][0][1], m["id"])
        t = J.load()["trades"][0]
        self.bn.set("FILLED", "NEW", "NEW", buy_px=float(t["entry"]))
        self.later(13 * 60)
        self.bot.monitor()
        ask = self.tg.sent[-1]
        self.assertIn("prazo", ask["text"])
        self.bot.monitor()                                        # asks only once
        self.assertIs(self.tg.sent[-1], ask)
        self.press(ask["buttons"][0][0][1], ask["id"], uid=5)
        self.assertEqual(len(self.bn.sent("/api/v3/orderList", "DELETE")), 1)
        sell = self.bn.sent("/api/v3/order")
        self.assertEqual(sell[0][2]["type"], "MARKET")
        self.assertEqual(sell[0][2]["quantity"], t["sell_qty"])
        self.assertEqual(J.load()["trades"][0]["reason"], "prazo")

    def test_pause_stops_new_proposals(self):
        self.pair()
        self.msg("/pausar", uid=3)
        self.assertTrue(self.bot.st["paused"])
        n = len(self.tg.sent)
        self.bot.scan_and_propose()
        self.assertEqual(len(self.tg.sent), n)

    def test_secrets_file_is_private(self):
        p = os.path.join(self.dir, "s.env")
        TB.save_secrets({"TELEGRAM_BOT_TOKEN": "t", "BINANCE_API_KEY": "a", "BINANCE_SECRET_KEY": "b"}, p)
        self.assertEqual(os.stat(p).st_mode & 0o777, 0o600)
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        TB.load_secrets(p)
        self.assertEqual(os.environ["TELEGRAM_BOT_TOKEN"], "t")


if __name__ == "__main__":
    unittest.main()
