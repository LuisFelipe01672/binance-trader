"""Painel (HTML servido pelo bot no proprio celular) e resumo diario do Telegram.

Read-only: the page shows data, it has no buttons that act on the account.
Everything is inline (no internet needed to draw it).
"""
import html
from datetime import timedelta

from . import common as c
from . import data
from . import ghost as G
from . import journal as J

E = html.escape


def _period_pnl(closed, now):
    day0 = now.astimezone(c.BRT).replace(hour=0, minute=0, second=0, microsecond=0)
    week0 = day0 - timedelta(days=day0.weekday())
    month0 = day0.replace(day=1)
    tot = lambda since: sum(t["pnl"] for t in closed if c.from_ms(t["closed"]) >= since)
    return tot(day0), tot(week0), tot(month0)


def real_stats(trades, now):
    closed = sorted([t for t in trades if t.get("closed") and not t.get("unfilled")], key=lambda t: t["closed"])
    d, w, m = _period_pnl(closed, now)
    out = {"closed": closed, "day": d, "week": w, "month": m, "n": len(closed),
           "open": [t for t in trades if not t.get("closed")]}
    if closed:
        out.update(win_rate=sum(1 for t in closed if t["pnl"] > 0) / len(closed),
                   avg_r=sum(t.get("r") or 0 for t in closed) / len(closed),
                   total=sum(t["pnl"] for t in closed))
    return out


# ---------------------------------------------------------------- svg chart
def line_chart(values, label_fmt, empty="sem dados ainda"):
    """Cumulative line, zero line, last value label. values: list of floats (already cumulative)."""
    w, h, pad = 640, 200, 28
    if len(values) < 2:
        return '<div class="empty">%s</div>' % E(empty)
    pts = [0.0] + list(values)
    lo, hi = min(pts + [0]), max(pts + [0])
    if hi - lo < 1e-9:
        hi, lo = hi + 1, lo - 1
    x = lambda i: pad + i * (w - 2 * pad) / (len(pts) - 1)
    y = lambda v: h - pad - (v - lo) * (h - 2 * pad) / (hi - lo)
    path = " ".join("%s%.1f,%.1f" % ("M" if i == 0 else "L", x(i), y(v)) for i, v in enumerate(pts))
    last = pts[-1]
    cls = "up" if last >= 0 else "down"
    return ('<svg viewBox="0 0 %d %d" class="chart" role="img" aria-label="curva">'
            '<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" class="zero"/>'
            '<path d="%s" class="line %s"/>'
            '<circle cx="%.1f" cy="%.1f" r="4" class="dot %s"/>'
            '<text x="%.1f" y="%.1f" class="lbl" text-anchor="end">%s</text>'
            '<text x="%d" y="%.1f" class="axis">0</text></svg>') % (
        w, h, pad, w - pad, y(0), y(0), path, cls, x(len(pts) - 1), y(last), cls,
        x(len(pts) - 1) - 8, y(last) - 10 if y(last) > 30 else y(last) + 20, E(label_fmt(last)), 4, y(0) + 4)


def _card(label, value, sub="", tone=""):
    return '<div class="card"><div class="k">%s</div><div class="v %s">%s</div><div class="s">%s</div></div>' % (
        E(label), tone, E(value), E(sub))


def _tone(v):
    return "up" if v > 0 else ("down" if v < 0 else "")


# ---------------------------------------------------------------- page
def render(cfg, bot_state, now=None, price=None):
    now = now or c.now()
    q = cfg["quote"]
    price = price or (lambda s: data.price(s))
    trades = J.load()["trades"]
    rs = real_stats(trades, now)
    ghosts = G.load()["ghosts"]
    gs = G.stats(ghosts)
    regime = c.read_json(c.path("regime.json")) or {}
    scan = c.read_json(c.path("scan.json")) or {}
    env = cfg["env"].upper()
    status = "pausado" if bot_state.get("paused") else "procurando"

    parts = []
    parts.append('<header><div><h1>Painel do bot</h1><div class="muted">Conta <b class="env %s">%s</b> · %s · atualizado %s</div></div></header>' % (
        "real" if cfg["env"] == "prod" else "", env, status, c.fmt_time(now)))

    # weather
    if regime:
        pos = regime["posture"]
        tone = {"risco-ligado": "up", "risco-desligado": "down"}.get(pos, "")
        parts.append('<section><h2>Clima do mercado</h2><div class="grid">%s%s%s</div>%s</section>' % (
            _card("Postura", pos.upper(), "placar %+d de 4" % regime["score"], tone),
            _card("BTC/USDT", "%.0f" % regime["btc"], "7 dias %s" % c.pct(regime["change_7d"]), _tone(regime["change_7d"])),
            _card("30 dias", c.pct(regime["change_30d"]), "mov. medio diario %s" % c.pct(regime["avg_daily_move"]), _tone(regime["change_30d"])),
            '<p class="note">Em risco-desligado o bot nao compra.</p>' if pos == "risco-desligado" else ""))

    # real results
    cards = [_card("Hoje", c.money(rs["day"], q), "", _tone(rs["day"])),
             _card("Semana", c.money(rs["week"], q), "", _tone(rs["week"])),
             _card("Mes", c.money(rs["month"], q), "", _tone(rs["month"]))]
    if rs["n"]:
        cards.append(_card("Acerto", c.pct(rs["win_rate"] * 100), "%d trades fechados" % rs["n"]))
        cards.append(_card("Media", "%+.2fR" % rs["avg_r"], "1R = perda no stop", _tone(rs["avg_r"])))
    cum, acc = [], 0.0
    for t in rs["closed"]:
        acc += t["pnl"]
        cum.append(acc)
    parts.append('<section><h2>Seus trades (%s)</h2><div class="grid">%s</div>%s</section>' % (
        env.lower(), "".join(cards),
        line_chart(cum, lambda v: c.money(v, q), "Nenhum trade fechado ainda. A curva aparece aqui.")))

    # open trades
    if rs["open"]:
        rows = []
        for t in rs["open"]:
            try:
                p = price(t["symbol"])
            except Exception:
                p = None
            e = float(t.get("entry_fill") or t["entry"])
            chg = (p / e - 1) * 100 if p else None
            rows.append("<tr><td>#%d %s</td><td>%s</td><td>%s</td><td>%s</td><td class='%s'>%s</td><td>%s</td></tr>" % (
                t["id"], E(t["symbol"]), c.price_str(e, q), c.price_str(t["stop"], q), c.price_str(t["target"], q),
                _tone(chg or 0), (c.price_str(p, q) + " (" + c.pct(chg, 2) + ")") if p else "-",
                "comprado" if t.get("filled") else "esperando compra"))
        parts.append('<section><h2>Abertos agora</h2><div class="scroll"><table><tr><th>Trade</th><th>Entrada</th>'
                     '<th>Stop</th><th>Alvo</th><th>Agora</th><th>Situacao</th></tr>%s</table></div></section>' % "".join(rows))

    # ghosts
    gcards = [_card("Sinais vistos", str(gs["total"]), "%d em andamento" % gs["open"])]
    if gs["n"]:
        gcards += [_card("Acerto", c.pct(gs["win_rate"] * 100), "%d resolvidos" % gs["n"]),
                   _card("Media", "%+.2fR" % gs["avg_r"], "depois das taxas", _tone(gs["avg_r"])),
                   _card("Soma", "%+.1fR" % gs["sum_r"], "se tivesse feito todos", _tone(gs["sum_r"]))]
    done = sorted([g for g in ghosts if g["status"] not in (G.OPEN, "nao executou")], key=lambda g: g["resolved"])
    gcum, acc = [], 0.0
    for g in done:
        acc += g["r"]
        gcum.append(acc)
    grows = "".join("<tr><td>%s</td><td>%d</td><td>%s</td><td class='%s'>%+.2fR</td></tr>" % (
        E(name), v["n"], c.pct(v["win_rate"] * 100), _tone(v["avg_r"]), v["avg_r"])
        for (kind, name), v in sorted(gs["groups"].items()))
    parts.append('<section><h2>Trades fantasmas</h2><p class="note">Todo sinal e acompanhado como se tivesse sido feito, '
                 'ate os bloqueados e os que voce recusou. Mostra se a estrategia funciona sem arriscar nada.</p>'
                 '<div class="grid">%s</div>%s%s</section>' % (
                     "".join(gcards),
                     line_chart(gcum, lambda v: "%+.1fR" % v, "Os resultados aparecem aqui conforme os sinais terminam."),
                     ('<div class="scroll"><table><tr><th>Grupo</th><th>Sinais</th><th>Acerto</th><th>Media</th></tr>%s</table></div>' % grows) if grows else ""))

    # recent signals
    recent = sorted(ghosts, key=lambda g: g["signal_time"], reverse=True)[:15]
    if recent:
        def res(g):
            if g["status"] == G.OPEN:
                return "<span class='muted'>em andamento</span>"
            if g["status"] == "nao executou":
                return "<span class='muted'>compra nao executaria</span>"
            return "<span class='%s nowrap'>%+.2fR</span><div class='muted small'>%s</div>" % (_tone(g["r"]), g["r"], g["status"])
        rows = "".join("<tr><td>%s</td><td><b>%s</b><div class='muted small'>%s</div></td><td>%s</td><td>%s</td></tr>" % (
            c.fmt_time(g["signal_time"]), E(g["symbol"][:-len(q)] if g["symbol"].endswith(q) else g["symbol"]), E(g["setup"]),
            E(G.group_of(g["decision"])) + ("<div class='muted small'>%s</div>" % E(g["reason"] or "") if g.get("reason") else ""),
            res(g)) for g in recent)
        parts.append('<section><h2>Ultimos sinais</h2><div class="scroll"><table><tr><th>Quando</th><th>Moeda</th>'
                     '<th>Decisao</th><th>Resultado</th></tr>%s</table></div></section>' % rows)

    # watchlist
    syms = scan.get("symbols") or []
    if syms:
        parts.append('<section><h2>Moedas vigiadas</h2><div class="chips">%s</div><p class="note">Ultima procura: %s, '
                     'velas de %s.</p></section>' % ("".join("<span>%s</span>" % E(s[:-len(q)] if s.endswith(q) else s) for s in syms),
                                                     c.fmt_time(scan["time"]), E(scan.get("interval", ""))))
    parts.append('<footer>%s A pagina se atualiza sozinha a cada minuto.</footer>' % E(c.DISCLAIMER))
    return PAGE.replace("{{BODY}}", "\n".join(parts))


PAGE = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="60">
<title>Painel do bot</title><style>
:root{--bg:#f6f7f9;--panel:#fff;--ink:#16181d;--muted:#667085;--line:#e4e7ec;--up:#0f8a5f;--down:#c4372d;--accent:#2f6fde}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--panel:#181b21;--ink:#e8eaed;--muted:#98a2b3;--line:#2a2f37;--up:#3ccf91;--down:#ff6b5e;--accent:#6ea0ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:760px;margin:0 auto;padding:16px}h1{font-size:22px;margin:0 0 2px}h2{font-size:16px;margin:0 0 10px}
header{margin:4px 0 14px}section{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px;margin:0 0 14px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:8px;margin-bottom:8px}
.card{border:1px solid var(--line);border-radius:10px;padding:10px}.k{color:var(--muted);font-size:12px}.v{font-size:20px;font-weight:650;font-variant-numeric:tabular-nums}
.s{color:var(--muted);font-size:12px}.up{color:var(--up)}.down{color:var(--down)}.muted{color:var(--muted)}.small{font-size:12px}
.env{padding:1px 6px;border-radius:6px;background:var(--line)}.env.real{background:var(--down);color:#fff}
.note{color:var(--muted);font-size:13px;margin:6px 0}.empty{color:var(--muted);font-size:13px;padding:24px 0;text-align:center;border:1px dashed var(--line);border-radius:10px}
.chart{width:100%;height:auto;display:block}.chart .line{fill:none;stroke-width:2.5}.chart .line.up{stroke:var(--up)}.chart .line.down{stroke:var(--down)}
.chart .zero{stroke:var(--line);stroke-dasharray:4 4}.chart .dot.up{fill:var(--up)}.chart .dot.down{fill:var(--down)}
.chart .lbl{fill:var(--ink);font-size:14px;font-weight:600}.chart .axis{fill:var(--muted);font-size:11px}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:12.5px;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:6px 4px;border-bottom:1px solid var(--line);vertical-align:top}td:first-child{white-space:nowrap}th{color:var(--muted);font-weight:500}
.nowrap{white-space:nowrap}td:last-child{white-space:nowrap}
.chips{display:flex;flex-wrap:wrap;gap:6px}.chips span{border:1px solid var(--line);border-radius:999px;padding:3px 10px;font-size:13px}
footer{color:var(--muted);font-size:12px;text-align:center;padding:8px 0 24px}
</style></head><body><main>{{BODY}}</main></body></html>"""


# ---------------------------------------------------------------- daily summary
def summary_text(cfg, now, bot_state=None):
    q = cfg["quote"]
    day0 = now.astimezone(c.BRT).replace(hour=0, minute=0, second=0, microsecond=0)
    rs = real_stats(J.load()["trades"], now)
    ghosts = G.load()["ghosts"]
    today = [g for g in ghosts if c.from_ms(g["signal_time"]) >= day0]
    resolved_today = [g for g in ghosts if g.get("resolved") and c.from_ms(g["resolved"]) >= day0
                      and g["status"] not in (G.OPEN, "nao executou")]
    gs = G.stats(ghosts)
    regime = c.read_json(c.path("regime.json")) or {}
    lines = ["Resumo de %s (%s)" % (c.brt(now).strftime("%d/%m"), cfg["env"].upper())]
    if regime:
        lines.append("Clima: %s, BTC 7 dias %s" % (regime["posture"].upper(), c.pct(regime["change_7d"])))
    lines.append("")
    lines.append("Seus trades hoje: %s | semana %s | mes %s" % (
        c.money(rs["day"], q), c.money(rs["week"], q), c.money(rs["month"], q)))
    if rs["open"]:
        lines.append("Abertos: %s" % ", ".join("#%d %s" % (t["id"], t["symbol"]) for t in rs["open"]))
    lines.append("")
    lines.append("Sinais vistos hoje: %d" % len(today))
    for g in today[-6:]:
        lines.append("- %s %s %s: %s" % (c.fmt_time(g["signal_time"])[-5:], g["symbol"], g["setup"], G.group_of(g["decision"])))
    if resolved_today:
        lines.append("Fantasmas que terminaram hoje: %d, soma %+.1fR" % (
            len(resolved_today), sum(g["r"] for g in resolved_today)))
    if gs["n"]:
        lines.append("Fantasmas desde o inicio: %d, acerto %s, media %+.2fR" % (
            gs["n"], c.pct(gs["win_rate"] * 100), gs["avg_r"]))
    lines.append("")
    lines.append("Painel: http://localhost:%d (abra no navegador deste celular)" % cfg.get("dashboard_port", 8765))
    return "\n".join(lines)
