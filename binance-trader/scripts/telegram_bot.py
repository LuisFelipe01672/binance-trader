#!/usr/bin/env python3
"""Bot do Telegram do binance-trader: procura trades sozinho, te manda o boleto com os
botoes Aprovar / Recusar, envia para a Binance so quando voce toca em Aprovar, e
acompanha o trade ate o fim (alvo, stop ou prazo).

  python3 telegram_bot.py setup     # pergunta token do bot e chaves da Binance (uma vez)
  python3 telegram_bot.py run       # liga o bot (deixe rodando)
  python3 telegram_bot.py check     # confere token, chaves e conexao, sem operar

Segredos ficam em ~/.binance-trader/secrets.env (permissao 600), nunca no codigo.
So o seu chat do Telegram, pareado com um codigo na primeira vez, pode aprovar.
Padrao: testnet (dinheiro de mentira).
"""
import contextlib
import getpass
import io
import json
import os
import random
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bt  # noqa: E402
from btlib import broker, common as c, data, journal as J, size  # noqa: E402

SECRETS = os.path.expanduser(os.environ.get("BT_SECRETS", "~/.binance-trader/secrets.env"))
TG_API = "https://api.telegram.org/bot%s/%s"
SECRET_KEYS = ("TELEGRAM_BOT_TOKEN", "BINANCE_API_KEY", "BINANCE_SECRET_KEY")


# ---------------------------------------------------------------- secrets
def load_secrets(p=SECRETS):
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
    except FileNotFoundError:
        pass


def save_secrets(values, p=SECRETS):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("# segredos do binance-trader: nao compartilhe este arquivo\n")
        for k in SECRET_KEYS:
            if values.get(k):
                f.write("%s=%s\n" % (k, values[k]))
    os.chmod(p, 0o600)


# ---------------------------------------------------------------- telegram
class Telegram:
    def __init__(self, token, http=None):
        self.token, self.http = token, http or c.http

    def call(self, method, _wait=30, **params):
        params = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in params.items() if v is not None}
        res = self.http("POST", TG_API % (self.token, method), params, timeout=_wait)
        if not res or not res.get("ok"):
            raise c.HttpError(0, "Telegram %s: %s" % (method, res))
        return res["result"]

    def send(self, chat, text, buttons=None):
        markup = {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in buttons]} if buttons else None
        return self.call("sendMessage", chat_id=chat, text=text[:4000], reply_markup=markup,
                         disable_web_page_preview="true")

    def edit(self, chat, message_id, text):
        try:
            self.call("editMessageText", chat_id=chat, message_id=message_id, text=text[:4000])
        except c.HttpError:
            pass

    def answer(self, callback_id, text=""):
        try:
            self.call("answerCallbackQuery", callback_query_id=callback_id, text=text[:190])
        except c.HttpError:
            pass

    def updates(self, offset, timeout=25):
        return self.call("getUpdates", _wait=timeout + 10, offset=offset, timeout=timeout,
                         allowed_updates=["message", "callback_query"])


# ---------------------------------------------------------------- the bot
def run_cli(*argv):
    """Runs a bt.py command, returns (ok, text)."""
    out = io.StringIO()
    ok = True
    with contextlib.redirect_stdout(out):
        try:
            bt.main([str(a) for a in argv])
        except SystemExit as e:
            if e.code not in (None, 0):
                ok = False
                print(e.code if isinstance(e.code, str) else "erro %s" % e.code)
    return ok, out.getvalue().strip()


class Bot:
    def __init__(self, tg, cfg=None, binance_http=None, extra=None, clock=None, log=print):
        self.tg = tg
        self.cfg = cfg or c.load_config()
        self.bhttp = binance_http          # None = real Binance
        self.extra = extra or {}           # extra CLI args per command (tests use offline files)
        self.clock = clock or (lambda: c.now())
        self.log = log
        self.st = c.read_json(c.path("state", "telegram.json"), None) or {
            "chat_id": None, "pair_code": None, "offset": None, "pending": {}, "paused": False,
            "last_scan": 0, "last_regime": 0, "last_monitor": 0, "last_error": 0, "deadline_asked": []}

    # -------------------------------------------------- state
    def save(self):
        c.write_json(c.path("state", "telegram.json"), self.st)

    def say(self, text, buttons=None):
        if self.st.get("chat_id"):
            return self.tg.send(self.st["chat_id"], text, buttons)

    def cli(self, cmd, *args):
        return run_cli(cmd, *(list(args) + list(self.extra.get(cmd, []))))

    # -------------------------------------------------- pairing
    def pair_code(self):
        if not self.st.get("pair_code"):
            self.st["pair_code"] = "%06d" % random.SystemRandom().randrange(10 ** 6)
            self.save()
        return self.st["pair_code"]

    # -------------------------------------------------- telegram updates
    def handle(self, upd):
        self.st["offset"] = upd["update_id"] + 1
        if "callback_query" in upd:
            self.on_button(upd["callback_query"])
        elif "message" in upd:
            self.on_message(upd["message"])
        self.save()

    def on_message(self, m):
        chat = m.get("chat", {}).get("id")
        text = (m.get("text") or "").strip()
        if not self.st.get("chat_id"):
            if text.startswith("/start") and text.split()[-1] == self.st.get("pair_code"):
                self.st["chat_id"] = chat
                self.st["pair_code"] = None
                self.save()
                self.tg.send(chat, "Pareado! So este chat pode aprovar ordens.\nConta: %s.\n\n%s" % (
                    self.cfg["env"].upper(), HELP))
            else:
                self.tg.send(chat, "Este bot ainda nao foi pareado. Envie /start seguido do codigo de 6 digitos "
                                   "que aparece no terminal onde o bot esta rodando.")
            return
        if chat != self.st["chat_id"]:
            return                                        # strangers are ignored silently
        cmd = text.split()[0].lower().split("@")[0] if text else ""
        if cmd in ("/status", "status"):
            self.cmd_status()
        elif cmd in ("/scan", "scan"):
            self.say("Procurando agora...")
            self.scan_and_propose(force=True)
        elif cmd in ("/diario", "/diário", "diario"):
            ok, out = self.cli("journal", "show", "--last", "10")
            self.say(out or "Diario vazio.")
        elif cmd in ("/pausar", "pausar"):
            self.st["paused"] = True
            self.say("Pausado: nao procuro trades novos. Os trades abertos continuam com stop e alvo na Binance. /retomar para voltar.")
        elif cmd in ("/retomar", "retomar"):
            self.st["paused"] = False
            self.say("Retomado. Procuro a cada vela de %s." % self.cfg["bot_interval"])
        elif cmd in ("/clima", "clima"):
            ok, out = self.cli("regime", "--write")
            self.say(out)
        else:
            self.say(HELP)

    def on_button(self, cb):
        chat = cb.get("message", {}).get("chat", {}).get("id")
        if not self.st.get("chat_id") or chat != self.st["chat_id"] or cb.get("from", {}).get("id") != chat:
            self.tg.answer(cb["id"], "Nao autorizado.")
            return
        action, _, key = (cb.get("data") or "").partition(":")
        msg_id = cb["message"]["message_id"]
        if action in ("ok", "no"):
            self.on_proposal_answer(cb["id"], msg_id, action, key)
        elif action in ("exit", "keep"):
            self.on_deadline_answer(cb["id"], msg_id, action, key)
        else:
            self.tg.answer(cb["id"])

    # -------------------------------------------------- proposals
    def scan_and_propose(self, force=False):
        if self.st["paused"] and not force:
            return
        if self.live_pending():
            if force:
                self.say("Ja tem uma proposta esperando a sua resposta.")
            return
        ok, out = self.cli("scan", "--interval", self.cfg["bot_interval"])
        if not ok:
            self.log("scan falhou: " + out)
            if force:
                self.say("Nao consegui procurar: " + out[-500:])
            return
        hits = [h for h in (c.read_json(c.path("scan.json"), {}) or {}).get("hits", []) if h["bars_ago"] == 0]
        skipped = []
        for h in hits:
            res = self.evaluate(h["symbol"], h["setup"])
            if res is True:
                return
            skipped.append("%s %s: %s" % (h["symbol"], h["setup"], res))
        if force:
            self.say("Nenhum trade passou nos filtros agora." + ("\n\nVistos e descartados:\n- " + "\n- ".join(skipped) if skipped else ""))
        elif skipped:
            self.log("descartados: " + "; ".join(skipped))

    def evaluate(self, symbol, setup):
        """plan -> sim -> size -> risk. Proposes and returns True, or returns why not."""
        ok, out = self.cli("plan", "--symbol", symbol, "--setup", setup, "--interval", self.cfg["bot_interval"])
        if not ok:
            return "plano: " + out.splitlines()[-1]
        ok, sim_out = self.cli("sim", "--write")
        if not ok:
            return "simulacao: " + sim_out.splitlines()[-1]
        sim = c.read_json(c.path("sim.json"), {}) or {}
        if self.cfg["bot_skip_no_edge"] and sim.get("verdict", "").startswith("SEM VANTAGEM"):
            return "simulacao sem vantagem"
        ok, out = self.cli("size")
        orders = (c.read_json(c.path("orders.json"), {}) or {}).get("orders", [])
        if not ok or not orders:
            return "tamanho: " + (out.splitlines()[-1] if out else "sem ordem")
        ok, risk_out = self.cli("risk")
        o = orders[0]
        r = (c.read_json(c.path("state", "risk.json"), {}) or {}).get(o["fingerprint"], {})
        if r.get("status") != "PASS":
            return "risco: " + "; ".join(r.get("reasons", [])) if r else "risco: sem resposta"
        warns = [l.strip()[7:] for l in risk_out.splitlines() if l.strip().startswith("aviso:")]
        self.propose(o, sim, warns)
        return True

    def propose(self, o, sim, warns):
        q = o["quote"]
        b, h = sim.get("bootstrap", {}), sim.get("history", {})
        lines = [
            "%s  %s  |  conta %s" % (o["symbol"], (o.get("setup") or "").upper(), self.cfg["env"].upper()),
            "",
            "COMPRAR %s %s a %s (ordem limite, ~%s)" % (o["qty"], o["base"], c.price_str(o["entry"], q), c.money(o["notional"], q)),
            "ALVO  %s  -> ganho ~%s" % (c.price_str(o["target"], q), c.money(o["target_gain"], q)),
            "STOP  %s  -> perda ~%s" % (c.price_str(o["stop"], q), c.money(o["max_loss"], q)),
            "Prazo: %dh. Taxas incluidas." % o["max_hold_hours"],
            "",
            "Maquina do tempo: alvo primeiro %s, stop primeiro %s, media %+.2fR" % (
                c.pct(b.get("alvo", 0) * 100), c.pct(b.get("stop", 0) * 100), b.get("avg_r", 0)),
        ]
        if h.get("n"):
            lines.append("Historico: %d sinais, acerto %s, media %+.2fR" % (h["n"], c.pct(h["win_rate"] * 100), h["avg_r"]))
        lines.append(sim.get("verdict", ""))
        for w in warns:
            lines.append("aviso: " + w)
        lines += ["", "Vale por %d min. Nada sai sem o seu toque em Aprovar." % self.cfg["approval_minutes"]]
        msg = self.say("\n".join(lines), [[("✅ Aprovar e enviar", "ok:" + o["fingerprint"]),
                                           ("❌ Recusar", "no:" + o["fingerprint"])]])
        self.st["pending"][o["fingerprint"]] = {"time": c.ms(self.clock()), "message_id": msg["message_id"] if msg else None,
                                                 "symbol": o["symbol"], "text": "\n".join(lines)}
        self.save()

    def live_pending(self):
        now = c.ms(self.clock())
        for fp, p in list(self.st["pending"].items()):
            if (now - p["time"]) / 60000 > self.cfg["approval_minutes"]:
                del self.st["pending"][fp]
                if p.get("message_id"):
                    self.tg.edit(self.st["chat_id"], p["message_id"], p["text"] + "\n\nEXPIROU: nada foi enviado.")
        self.save()
        return bool(self.st["pending"])

    def on_proposal_answer(self, cb_id, msg_id, action, fp):
        p = self.st["pending"].pop(fp, None)
        self.save()
        if not p:
            self.tg.answer(cb_id, "Essa proposta ja expirou ou foi respondida.")
            return
        if action == "no":
            self.tg.answer(cb_id, "Recusado.")
            self.tg.edit(self.st["chat_id"], msg_id, p["text"] + "\n\nRECUSADO por voce. Nada foi enviado.")
            return
        now = self.clock()
        if (c.ms(now) - p["time"]) / 60000 > self.cfg["approval_minutes"]:
            self.tg.answer(cb_id, "Expirou.")
            self.tg.edit(self.st["chat_id"], msg_id, p["text"] + "\n\nEXPIROU: nada foi enviado.")
            return
        orders = (c.read_json(c.path("orders.json"), {}) or {}).get("orders", [])
        idx = next((n for n, o in enumerate(orders, 1) if o["fingerprint"] == fp), None)
        if not idx:
            self.tg.answer(cb_id, "Ordem nao encontrada.")
            return
        self.tg.answer(cb_id, "Conferindo e enviando...")
        ok, risk_out = self.cli("risk")                    # check again with the price of now
        r = (c.read_json(c.path("state", "risk.json"), {}) or {}).get(fp, {})
        if r.get("status") != "PASS":
            self.tg.edit(self.st["chat_id"], msg_id, p["text"] + "\n\nNAO ENVIADO: o risco mudou: " + "; ".join(r.get("reasons", [])))
            return
        # the tap on Aprovar is the explicit yes; real money still needs allow_live + BT_ALLOW_LIVE=1
        args = type("A", (), {"now": now.isoformat(), "index": idx, "reply": "sim",
                              "live": os.environ.get("BT_ALLOW_LIVE") == "1", "dry_run": False})()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                broker.approve(args, self.cfg)
                res = broker.place(args, self.cfg, http=self.bhttp)
        except SystemExit as e:
            self.tg.edit(self.st["chat_id"], msg_id, p["text"] + "\n\nNAO ENVIADO: %s" % e.code)
            return
        except c.HttpError as e:
            self.tg.edit(self.st["chat_id"], msg_id, p["text"] + "\n\nA BINANCE RECUSOU: %s" % e)
            return
        ref = res.get("orderListId", res.get("orderId"))
        self.tg.edit(self.st["chat_id"], msg_id, p["text"] + "\n\nENVIADO (%s), lista %s. Te aviso quando executar." % (self.cfg["env"], ref))

    # -------------------------------------------------- monitoring open trades
    def binance(self, method, endpoint, params):
        return broker.signed(method, self.cfg["env"], endpoint, params, http=self.bhttp)

    def monitor(self):
        now = self.clock()
        q = self.cfg["quote"]
        for t in J.load()["trades"]:
            if t.get("closed") or t.get("env") != self.cfg["env"] or t.get("broker_ref") is None:
                continue
            lst = self.binance("GET", "/api/v3/orderList", {"orderListId": t["broker_ref"]})
            orders = [self.binance("GET", "/api/v3/order", {"symbol": o["symbol"], "orderId": o["orderId"]})
                      for o in lst.get("orders", [])]
            buy = next((o for o in orders if o["side"] == "BUY"), None)
            sells = [o for o in orders if o["side"] == "SELL"]
            age_min = (c.ms(now) - t["opened"]) / 60000
            if buy and buy["status"] in ("NEW", "PARTIALLY_FILLED") and float(buy["executedQty"]) == 0:
                if age_min > self.cfg["entry_timeout_min"]:
                    self.binance("DELETE", "/api/v3/orderList", {"symbol": t["symbol"], "orderListId": t["broker_ref"]})
                    J.cancel_unfilled(t["id"], now)
                    self.say("#%d %s: a compra a %s nao executou em %d min. Cancelei. Nada foi comprado." % (
                        t["id"], t["symbol"], c.price_str(t["entry"], q), self.cfg["entry_timeout_min"]))
                continue
            if buy and buy["status"] in ("CANCELED", "EXPIRED", "REJECTED") and float(buy["executedQty"]) == 0:
                J.cancel_unfilled(t["id"], now, "compra %s" % buy["status"].lower())
                self.say("#%d %s: a compra foi %s na Binance. Nada foi comprado." % (t["id"], t["symbol"], buy["status"]))
                continue
            entry_fill = None
            if buy and float(buy["executedQty"]) > 0:
                entry_fill = float(buy["cummulativeQuoteQty"]) / float(buy["executedQty"])
                if not t.get("filled"):
                    J.update(t["id"], filled=c.ms(now), entry_fill=entry_fill)
                    self.say("#%d %s: COMPROU %s a %s. Stop %s e alvo %s ja estao na Binance." % (
                        t["id"], t["symbol"], t["qty"], c.price_str(entry_fill, q),
                        c.price_str(t["stop"], q), c.price_str(t["target"], q)))
            done = next((o for o in sells if o["status"] == "FILLED"), None)
            if done:
                px = float(done["cummulativeQuoteQty"]) / float(done["executedQty"])
                which = "alvo" if done["type"] in ("LIMIT_MAKER", "LIMIT", "TAKE_PROFIT", "TAKE_PROFIT_LIMIT") else "stop"
                tr = J.finalize(t["id"], px, self.cfg, now, entry_fill=entry_fill, reason=which)
                self.say("#%d %s: %s a %s. Resultado %s (%+.2fR).%s" % (
                    t["id"], t["symbol"], "BATEU O ALVO" if which == "alvo" else "SAIU NO STOP",
                    c.price_str(px, q), c.money(tr["pnl"], q), tr["r"] or 0,
                    "" if tr["pnl"] >= 0 else "\nPausa de %d min antes do proximo trade." % self.cfg["revenge_cooldown_min"]))
                continue
            if entry_fill and age_min > t.get("max_hold_hours", self.cfg["max_hold_hours"]) * 60 \
                    and t["id"] not in self.st["deadline_asked"]:
                self.st["deadline_asked"].append(t["id"])
                self.save()
                self.say("#%d %s: o prazo de %dh venceu e nem alvo nem stop executaram.\n"
                         "Sair agora vende %s a mercado (cancela o stop e o alvo antes)." % (
                             t["id"], t["symbol"], self.cfg["max_hold_hours"], t["sell_qty"]),
                         [[("🚪 Sair agora", "exit:%d" % t["id"]), ("Manter", "keep:%d" % t["id"])]])

    def on_deadline_answer(self, cb_id, msg_id, action, key):
        tid = int(key)
        t = next((t for t in J.load()["trades"] if t["id"] == tid), None)
        if not t or t.get("closed"):
            self.tg.answer(cb_id, "Esse trade ja terminou.")
            return
        if action == "keep":
            self.tg.answer(cb_id, "Mantido. Stop e alvo continuam na Binance.")
            self.tg.edit(self.st["chat_id"], msg_id, "#%d %s: mantido por voce. Stop e alvo continuam ativos." % (tid, t["symbol"]))
            return
        self.tg.answer(cb_id, "Saindo...")
        now = self.clock()
        try:
            self.binance("DELETE", "/api/v3/orderList", {"symbol": t["symbol"], "orderListId": t["broker_ref"]})
            rules = data.symbol_rules(t["symbol"], offline=(self.extra.get("rules") or [None])[0])
            qty = c.floor_step(t["sell_qty"], rules["step"])
            res = self.binance("POST", "/api/v3/order", {"symbol": t["symbol"], "side": "SELL", "type": "MARKET",
                                                          "quantity": c.dstr(qty)})
            px = float(res["cummulativeQuoteQty"]) / float(res["executedQty"])
            tr = J.finalize(tid, px, self.cfg, now, reason="prazo")
            self.tg.edit(self.st["chat_id"], msg_id, "#%d %s: VENDIDO a mercado a %s. Resultado %s (%+.2fR)." % (
                tid, t["symbol"], c.price_str(px, t["quote"]), c.money(tr["pnl"], t["quote"]), tr["r"] or 0))
        except (c.HttpError, SystemExit) as e:
            self.tg.edit(self.st["chat_id"], msg_id, "#%d %s: NAO CONSEGUI SAIR: %s\nConfira no app da Binance." % (
                tid, t["symbol"], getattr(e, "code", e)))

    def cmd_status(self):
        trades = [t for t in J.load()["trades"] if not t.get("closed")]
        lines = ["Conta %s%s." % (self.cfg["env"].upper(), " (pausado)" if self.st["paused"] else "")]
        if not trades:
            lines.append("Nenhum trade aberto.")
        for t in trades:
            lines.append("#%d %s %s @ %s, stop %s, alvo %s, %s" % (
                t["id"], t["symbol"], t["qty"], c.price_str(t.get("entry_fill") or t["entry"], t["quote"]),
                c.price_str(t["stop"], t["quote"]), c.price_str(t["target"], t["quote"]),
                "comprado" if t.get("filled") else "esperando a compra executar"))
        ok, out = self.cli("journal", "show", "--last", "1")
        if "Fechados" in out:
            lines += ["", out.split("\n\n")[-1]]
        self.say("\n".join(lines))

    # -------------------------------------------------- the loop
    def tick(self):
        """Work that runs between Telegram polls."""
        now = c.ms(self.clock())
        try:
            if now - self.st["last_monitor"] >= 60000:
                self.st["last_monitor"] = now
                self.monitor()
            if now - self.st["last_regime"] >= 6 * 3600000:
                ok, _ = self.cli("regime", "--write")
                if ok:
                    self.st["last_regime"] = now
            iv = data.INTERVAL_MS[self.cfg["bot_interval"]]
            if now // iv > self.st["last_scan"] // iv and now % iv >= 30000:   # 30 s after each candle closes
                self.st["last_scan"] = now
                self.scan_and_propose()
            self.live_pending()
        except Exception as e:                                           # keep running, tell the owner
            self.log(traceback.format_exc())
            if now - self.st.get("last_error", 0) > 3600000:
                self.st["last_error"] = now
                self.say("Erro no bot (ele continua rodando): %s" % str(e)[:300])
        self.save()

    def poll(self):
        for upd in self.tg.updates(self.st.get("offset")):
            self.handle(upd)


HELP = ("Comandos:\n/status trades abertos e resultado\n/scan procurar agora\n/clima clima do mercado\n"
        "/diario ultimos trades\n/pausar parar de procurar\n/retomar voltar a procurar\n\n"
        "Eu procuro sozinho a cada vela e te mando quando algo passar em todos os filtros. "
        "Ferramenta educacional, nao e recomendacao de investimento.")


# ---------------------------------------------------------------- commands
def ensure_config():
    p = c.path("config.json")
    if not os.path.exists(p):
        cfg = c.read_json(os.path.join(HERE, "..", "config.bot.example.json"))
        cfg = {k: v for k, v in cfg.items() if not k.startswith("_")}
        c.write_json(p, cfg)
        print("Config criada em %s (testnet, USDT). Ajuste os limites se quiser." % p)


def cmd_setup():
    print("Configuracao do bot. Nada do que voce digitar aparece na tela nem sai deste aparelho.\n")
    vals = {}
    vals["TELEGRAM_BOT_TOKEN"] = getpass.getpass("Token do bot (do @BotFather): ").strip()
    vals["BINANCE_API_KEY"] = getpass.getpass("API Key da Binance (testnet): ").strip()
    vals["BINANCE_SECRET_KEY"] = getpass.getpass("Secret Key da Binance (testnet): ").strip()
    save_secrets(vals)
    print("\nSalvo em %s (so voce le)." % SECRETS)
    ensure_config()
    cmd_check()


def cmd_check():
    load_secrets()
    cfg = c.load_config()
    print("Conta: %s (%s)" % (cfg["env"], c.ENV_URLS[cfg["env"]]))
    tok = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not tok:
        print("FALTA o token do Telegram: rode setup")
    else:
        try:
            me = Telegram(tok).call("getMe")
            print("Telegram OK: @%s" % me["username"])
        except c.HttpError as e:
            print("Telegram FALHOU: %s" % e)
    try:
        acct = broker.signed("GET", cfg["env"], "/api/v3/account", {"omitZeroBalances": "true"})
        bal = {b["asset"]: float(b["free"]) for b in acct.get("balances", [])}
        print("Binance OK. Saldo %s: %s" % (cfg["quote"], bal.get(cfg["quote"], 0)))
        if acct.get("canWithdraw"):
            print("ATENCAO: esta chave pode SACAR. Crie uma chave sem permissao de saque.")
    except (c.HttpError, SystemExit) as e:
        print("Binance FALHOU: %s" % getattr(e, "code", e))
    try:
        print("Preco BTC%s: %s" % (cfg["quote"], data.price("BTC" + cfg["quote"])))
    except c.HttpError as e:
        print("Dados de mercado FALHARAM: %s" % e)


def cmd_run():
    load_secrets()
    ensure_config()
    tok = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not tok:
        raise SystemExit("falta o token: rode  python3 telegram_bot.py setup")
    stamp = lambda: time.strftime("%H:%M:%S")
    bot = Bot(Telegram(tok), log=lambda m: print("[%s] %s" % (stamp(), m), flush=True))
    if not bot.st.get("chat_id"):
        print("\n>>> Abra o seu bot no Telegram e envie:   /start %s\n" % bot.pair_code(), flush=True)
    else:
        bot.say("Bot ligado. Conta %s. /ajuda para os comandos." % bot.cfg["env"].upper())
    print("[%s] rodando (Ctrl+C para parar). Conta %s." % (stamp(), bot.cfg["env"]), flush=True)
    while True:
        try:
            bot.poll()
            bot.tick()
        except KeyboardInterrupt:
            print("parado.")
            return
        except Exception as e:
            print("[%s] erro: %s (tento de novo em 10 s)" % (stamp(), e), flush=True)
            time.sleep(10)


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "help"
    {"setup": cmd_setup, "run": cmd_run, "check": cmd_check}.get(what, lambda: print(__doc__))()
