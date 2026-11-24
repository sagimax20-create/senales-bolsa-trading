"""Calculos y HTML compartidos por los reportes (scripts 5, 6 y 7).

Retorno neto (PROTOCOLO §6), sobre operaciones CERRADAS de un canal:
  capital operado  = n_cerradas * $1,000                       ($1,000 por señal, §2)
  p&l neto         = suma(1,000 * retorno_neto_i)               (ya descuenta la comision, §4)
  costo bancario   = como el ejemplo del §4: se deposita capital_operado / 0.92 (el banco
                     descuenta 8% y se opera con el 92%) y al retirar el banco descuenta 10%
  retorno neto     = (lo que se recibe - lo depositado) / lo depositado
  Ejemplo §4: deposito $10,000 -> opera $9,200 -> retira $11,200 -> recibe $10,080 -> +0.8%.
Hitos (PROTOCOLO §5) por canal: Vicente Luz cuenta señales validas; Gravity Zone, alertas.
Gravity Zone se suspendio el 2026-11-24 (ADENDA-02): con gravity_enabled = false los reportes solo
muestran Vicente Luz y una nota con el historico de Gravity Zone.
"""
from __future__ import annotations

import html
from collections import defaultdict
from datetime import date, datetime, timedelta

from common_utils import (GRAVITY_COLS, GRAVITY_CSV, MESSAGES_COLS, MESSAGES_CSV, NY,
                          OPERATIONS_CSV, OPS_COLS, SIGNALS_COLS, SIGNALS_CSV, fmt_ts, load_csv,
                          now_utc, parse_ts, to_float)
from ops_engine import PROFIT_LEVELS

CHANNELS = [("VICENTE_LUZ", "Vicente Luz"), ("GRAVITY_ZONE", "Gravity Zone")]


def channels(cfg: dict) -> list[tuple[str, str]]:
    """Canales activos del testeo."""
    return [c for c in CHANNELS if c[0] != "GRAVITY_ZONE" or cfg.get("gravity_enabled", True)]
REASONS = [("take_profit", "Take-profit +50%"), ("stop_loss", "Stop-loss −50%"), ("expiry", "Vencimiento 45 días")]
MOTIVOS = {
    "repeticion": "Repetición de un contrato ya publicado",
    "sin_estructura": "Ticker y dirección, pero sin strike, vencimiento ni prima",
    "cierre_o_venta": "Mensaje de cierre o venta, no de entrada",
    "direccion_ambigua": "CALL y PUT del mismo ticker en el mismo mensaje",
    "pierna_de_spread": "Pierna vendida de un spread (la señal es la pierna comprada)",
    "venta_de_opciones": "Venta de opciones sin compra (el protocolo solo compra)",
    "mismo_subyacente": "Mismo subyacente que otra señal del mensaje (XSP = SPX / 10)",
    "minusculas_strike_incoherente": "Ticker en minúsculas con strike incoherente con su precio",
    "minusculas_sin_precio": "Ticker en minúsculas sin precio para verificarlo",
    "minusculas_sin_strike": "Ticker en minúsculas sin strike para verificarlo",
}
SUPUESTOS = (
    "Supuestos del modelo: las primas son teóricas (Black-Scholes con volatilidad realizada de 20 días "
    "como IV y tasa libre de riesgo de config.json) porque el pipeline no consulta cotizaciones reales "
    "de opciones. El contrato es el del protocolo (OTM 95%, 45 días), no el que publica el canal. "
    "Entrada en la apertura de la primera vela de 15 minutos después de la señal; +50% / −50% revisados "
    "vela por vela. "
    "Dinero ficticio. Esto es análisis de datos, no asesoría financiera."
)


# --------------------------------------------------------------------------- datos
def load_data(cfg: dict | None = None) -> dict:
    """Datos para los reportes. Con Gravity Zone suspendido, `ops` solo trae Vicente Luz y el
    historico de Gravity Zone queda en `gravity_hist`."""
    signals = load_csv(SIGNALS_CSV, SIGNALS_COLS)
    ops = load_csv(OPERATIONS_CSV, OPS_COLS)
    active = {ch for ch, _ in channels(cfg or {})}
    return {
        "signals": signals,
        "valid": [s for s in signals if s.get("estado") == "valida"],
        "gravity": load_csv(GRAVITY_CSV, GRAVITY_COLS),
        "ops": [o for o in ops if o["channel"] in active],
        "gravity_hist": [o for o in ops if o["channel"] == "GRAVITY_ZONE"],
        "messages": load_csv(MESSAGES_CSV, MESSAGES_COLS),
    }


def suspended_note(data: dict, cfg: dict) -> str:
    """Nota de Gravity Zone suspendido (ADENDA-02), con su historico."""
    if cfg.get("gravity_enabled", True):
        return ""
    hist = data.get("gravity_hist", [])
    st = summarize(hist, cfg)
    return (f"<p class='muted'>Gravity Zone: suspendido desde el 2026-11-24 (docs/ADENDA-02-GRAVITY.md). "
            f"Histórico: {len(data.get('gravity', []))} alertas, {st['n_closed']} operaciones cerradas "
            f"(take-profit {st['by_reason']['take_profit']['n']}, stop-loss {st['by_reason']['stop_loss']['n']}), "
            f"neto promedio {esc(pct(st['avg_net']))}. No cuenta para hitos ni resultados.</p>")


def channel_signals(data: dict, channel: str, start: str = "", end: str = "") -> list[dict]:
    """Señales validas del canal con timestamp en [start, end)."""
    rows = data["valid"] if channel == "VICENTE_LUZ" else data["gravity"]
    return sorted((r for r in rows if (not start or r["timestamp"] >= start) and (not end or r["timestamp"] < end)),
                  key=lambda r: r["timestamp"])


def ny_bounds(d0: date, d1: date) -> tuple[str, str]:
    """Fechas de Nueva York [d0, d1) como timestamps UTC de texto."""
    return (fmt_ts(datetime(d0.year, d0.month, d0.day, tzinfo=NY)),
            fmt_ts(datetime(d1.year, d1.month, d1.day, tzinfo=NY)))


# --------------------------------------------------------------------------- calculos
def bank_adjusted(n_closed: int, pnl_usd: float, cfg: dict) -> dict:
    cap = float(cfg["protocol_capital_per_signal"])
    fee_in, fee_out = float(cfg["protocol_bank_cost_entry"]), float(cfg["protocol_bank_cost_exit"])
    operated = n_closed * cap
    deposit = operated / (1 - fee_in) if operated else 0.0
    withdraw = max(operated + pnl_usd, 0.0)
    received = withdraw * (1 - fee_out)
    return {"operado": operated, "deposito": deposit, "retiro": withdraw, "recibe": received,
            "costo_banco": deposit * fee_in + withdraw * fee_out,
            "ret_con_banco": (received - deposit) / deposit * 100 if deposit else None}


def _net(o: dict) -> float:
    return to_float(o.get("retorno_neto_pct"), 0.0)


def summarize(ops: list[dict], cfg: dict) -> dict:
    cap = float(cfg["protocol_capital_per_signal"])
    closed = [o for o in ops if o["estado"] == "closed"]
    nets = [_net(o) for o in closed]
    pnl = sum(cap * x / 100 for x in nets)
    by_reason = {}
    for key, _label in REASONS:
        sub = [_net(o) for o in closed if o["reason_exit"] == key]
        by_reason[key] = {"n": len(sub), "avg": sum(sub) / len(sub) if sub else None,
                          "pnl": sum(cap * x / 100 for x in sub)}
    tp_done = [o for o in closed if o["reason_exit"] == "take_profit" and str(o.get("sombra_completa")) == "1"]
    st = {
        "n_ops": len(ops), "n_closed": len(closed),
        "n_open": sum(o["estado"] == "open" for o in ops),
        "n_pending": sum(o["estado"] == "pending" for o in ops),
        "n_sin_precio": sum(o["estado"] == "sin_precio" for o in ops),
        "avg_net": sum(nets) / len(nets) if nets else None,
        "win_rate": sum(x > 0 for x in nets) / len(nets) * 100 if nets else None,
        "pnl_usd": pnl,
        "comisiones_usd": sum(cap * to_float(o.get("comision_pct"), 0.0) / 100 for o in closed),
        "ret_sin_banco": pnl / (len(closed) * cap) * 100 if closed else None,
        "by_reason": by_reason,
        "nota2": {"tp_con_sombra": len(tp_done), "toco_100": sum(str(o.get("toco_100")) == "1" for o in tp_done),
                  "toco_150": sum(str(o.get("toco_150")) == "1" for o in tp_done),
                  "toco_200": sum(str(o.get("toco_200")) == "1" for o in tp_done)},
    }
    st.update(bank_adjusted(len(closed), pnl, cfg))
    return st


def group_table(ops: list[dict], keyfn) -> dict:
    g = defaultdict(list)
    for o in ops:
        if o["estado"] == "closed":
            g[keyfn(o)].append(_net(o))
    return {k: {"n": len(v), "avg": sum(v) / len(v), "win": sum(x > 0 for x in v) / len(v) * 100}
            for k, v in sorted(g.items())}


def ops_of(data: dict, channel: str, original_only: bool = False) -> list[dict]:
    return [o for o in data["ops"] if o["channel"] == channel and
            (not original_only or str(o.get("en_universo_original")) == "1")]


def projection(sigs: list[dict], target: int, now: datetime) -> str:
    n = len(sigs)
    if n >= target:
        return "alcanzado"
    if n < 5:
        return "sin dato (pocas señales para proyectar)"
    days = max((now - parse_ts(sigs[0]["timestamp"])).total_seconds() / 86400, 1)
    eta = now + timedelta(days=(target - n) / (n / days))
    return f"{eta:%Y-%m-%d} (proyección al ritmo actual: {n / days * 7:.1f} por semana)"


def milestones(data: dict, channel: str, cfg: dict, now: datetime | None = None) -> dict:
    """Estado de la alarma (100) y de la decision (300) de un canal."""
    now = now or now_utc()
    alarm, final = int(cfg["protocol_hito_alarm"]), int(cfg["protocol_hito_decision"])
    sigs = channel_signals(data, channel, start=cfg["capture_start"] + " 00:00:00")
    ops = ops_of(data, channel)
    st = summarize(ops, cfg)
    n = len(sigs)
    thr = cfg["alert_return_threshold"] * 100
    if n < alarm:
        a_state, a_action = f"EN PROGRESO ({n}/{alarm})", "Seguir capturando."
    elif st["ret_sin_banco"] is None:
        a_state, a_action = f"ALCANZADA ({n}/{alarm})", "Aún no hay operaciones cerradas para evaluar."
    elif st["ret_sin_banco"] < thr:
        a_state, a_action = f"ALCANZADA ({n}/{alarm})", f"REVISAR PROTOCOLO: retorno sin costo bancario < {thr:.0f}%."
    else:
        a_state, a_action = f"ALCANZADA ({n}/{alarm})", f"CONTINUAR: retorno sin costo bancario ≥ {thr:.0f}%."
    verdict, decision_ops = None, []
    if n < final:
        f_state = f"PENDIENTE ({n}/{final})"
        f_action = f"Si el retorno neto con costo bancario es < {cfg['protocol_min_return'] * 100:.0f}%, se rechaza."
    else:
        if channel == "VICENTE_LUZ":
            keys = {f"VICENTE_LUZ|{s['signal_id']}" for s in sigs[:final]}
        else:
            keys = {f"GRAVITY_ZONE|{s['timestamp']}|{s['ticker']}|{s['signal_type']}" for s in sigs[:final]}
        decision_ops = [o for o in ops if o["signal_key"] in keys]
        pending = [o for o in decision_ops if o["estado"] != "closed"]
        dst = summarize(decision_ops, cfg)
        if pending:
            f_state = f"ALCANZADA ({n}/{final}); faltan cerrar {len(pending)} de las primeras {final} operaciones"
            f_action = "Esperar el cierre para el veredicto."
        else:
            ok = dst["ret_con_banco"] is not None and dst["ret_con_banco"] >= cfg["protocol_min_return"] * 100
            verdict = "VALIDADA" if ok else "RECHAZADA"
            f_state = f"ALCANZADA ({n}/{final}); veredicto con las primeras {final} señales"
            f_action = (f"{verdict}: retorno neto con costo bancario {pct(dst['ret_con_banco'])} "
                        f"({'candidata a dinero real' if ok else 'no pasa a dinero real'}).")
    return {"n": n, "alarm": alarm, "final": final, "a_state": a_state, "a_action": a_action,
            "f_state": f_state, "f_action": f_action, "verdict": verdict, "stats": st,
            "eta_alarm": projection(sigs, alarm, now), "eta_final": projection(sigs, final, now)}


# --------------------------------------------------------------------------- formato
def pct(x, nd=1, plus=True) -> str:
    return "sin dato" if x is None else (f"{x:+.{nd}f}%" if plus else f"{x:.{nd}f}%")


def usd(x) -> str:
    return "sin dato" if x is None else f"{'-' if x < 0 else ''}${abs(x):,.0f}"


def esc(x) -> str:
    return html.escape("" if x is None else str(x))


def cls(x) -> str:
    return "" if x is None else ("pos" if x > 0 else "neg" if x < 0 else "")


CSS = """
body{margin:0;background:#ffffff;color:#0B1F3A;font-family:Arial,Helvetica,sans-serif;line-height:1.45;font-size:14px}
header{background:#0B1F3A;color:#ffffff;padding:18px 22px;border-bottom:4px solid #C9A227}
header h1{margin:0;font-size:21px}header p{margin:4px 0 0;color:#E8D48A}
main{max-width:1000px;margin:0 auto;padding:12px 18px 32px}
h2{border-bottom:2px solid #C9A227;padding-bottom:4px;margin-top:26px;font-size:18px}
h3{font-size:15px;margin:16px 0 4px}
.kpis{display:flex;flex-wrap:wrap;gap:10px}.kpi{flex:1 1 140px;border:1px solid #0B1F3A;border-top:4px solid #C9A227;padding:8px 12px}
.kpi b{display:block;font-size:19px}.kpi span{font-size:12px;color:#4a5a73}
table{border-collapse:collapse;width:100%;margin-top:6px;font-size:13px}
th{background:#0B1F3A;color:#ffffff;text-align:left;padding:5px 8px}td{padding:5px 8px;border-bottom:1px solid #d9dee6}
.wrap{overflow-x:auto}.neg{color:#B3261E}.pos{color:#1B6E3C}.muted{color:#4a5a73;font-size:12px}
.bar{background:#e6e9ef;height:10px;border-radius:5px;overflow:hidden;margin:4px 0 2px}.bar i{display:block;height:100%;background:#C9A227}
.alert{background:#B3261E;color:#ffffff;padding:10px 14px;margin-top:14px;font-weight:bold}
.note{font-size:12px;color:#4a5a73;margin-top:24px;border-top:1px solid #d9dee6;padding-top:10px}
"""


def page(title: str, subtitle: str, body: str) -> str:
    return (f"<!doctype html><html lang='es'><head><meta charset='utf-8'><meta name='viewport' "
            f"content='width=device-width,initial-scale=1'><title>{esc(title)}</title><style>{CSS}</style></head>"
            f"<body><header><h1>{esc(title)}</h1><p>{esc(subtitle)}</p></header><main>{body}"
            f"<p class='note'>{esc(SUPUESTOS)}</p></main></body></html>")


def table(headers: list[str], rows: list[list], empty: str = "Sin datos.") -> str:
    if not rows:
        return f"<p class='muted'>{esc(empty)}</p>"
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(c if isinstance(c, str) and c.startswith("<td") else f"<td>{esc(c)}</td>"
                                    for c in r) + "</tr>" for r in rows)
    return f"<div class='wrap'><table><tr>{head}</tr>{body}</table></div>"


def td_pct(x) -> str:
    return f"<td class='{cls(x)}'>{esc(pct(x))}</td>"


def kpis(items: list[tuple[str, str]]) -> str:
    return "<div class='kpis'>" + "".join(f"<div class='kpi'><b>{esc(v)}</b><span>{esc(k)}</span></div>"
                                          for k, v in items) + "</div>"


def progress(label: str, n: int, target: int) -> str:
    w = min(n / target * 100, 100) if target else 0
    return (f"<div><b>{esc(label)}</b>: {n} de {target} ({w:.0f}%)"
            f"<div class='bar'><i style='width:{w:.1f}%'></i></div></div>")


def milestones_html(data: dict, cfg: dict, now: datetime, week_counts: dict | None = None) -> str:
    out = []
    for ch, name in channels(cfg):
        m = milestones(data, ch, cfg, now)
        extra = f" · esta semana: {week_counts[ch]}" if week_counts else ""
        out.append(f"<h3>{esc(name)}</h3><p>Señales válidas acumuladas: <b>{m['n']}</b>{esc(extra)}</p>"
                   f"{progress('Alarma', m['n'], m['alarm'])}<p class='muted'>Fecha estimada: {esc(m['eta_alarm'])}"
                   f" · {esc(m['a_action'])}</p>{progress('Decisión final', m['n'], m['final'])}"
                   f"<p class='muted'>Fecha estimada: {esc(m['eta_final'])} · {esc(m['f_state'])}. {esc(m['f_action'])}</p>")
    return "".join(out) + suspended_note(data, cfg)


def results_html(data: dict, cfg: dict) -> str:
    rows, reason_rows = [], []
    views = [(name, ops_of(data, ch)) for ch, name in channels(cfg)]
    views.append(("Vicente Luz · solo los 8 tickers originales", ops_of(data, "VICENTE_LUZ", original_only=True)))
    for name, ops in views:
        st = summarize(ops, cfg)
        rows.append([name, st["n_closed"], st["n_open"], st["n_pending"] + st["n_sin_precio"],
                     td_pct(st["avg_net"]), pct(st["win_rate"], 0, False), usd(st["pnl_usd"]),
                     usd(st["comisiones_usd"]), td_pct(st["ret_sin_banco"]),
                     usd(st["costo_banco"] if st["n_closed"] else None), td_pct(st["ret_con_banco"])])
        for key, label in REASONS:
            br = st["by_reason"][key]
            reason_rows.append([name, label, br["n"], td_pct(br["avg"]), usd(br["pnl"])])
    return ("<h3>Retorno neto acumulado (operaciones cerradas)</h3>"
            + table(["Canal", "Cerradas", "Abiertas", "Pendientes / sin precio", "Neto prom. por operación",
                     "% ganadoras", "P&L neto", "Comisiones", "Retorno sin costo bancario", "Costo bancario",
                     "Retorno neto con costo bancario"], rows)
            + "<p class='muted'>El criterio de rechazo del protocolo (§6) usa el retorno neto con costo bancario. "
              "El costo bancario sigue el ejemplo del §4 (8% al depositar, 10% al retirar).</p>"
            + "<h3>Resultado por regla de salida</h3>"
            + table(["Canal", "Regla", "Operaciones", "Neto prom.", "P&L neto"], reason_rows))


def _d(ts: str) -> str:
    return ts[:16] if ts else ""


def ops_rows(ops: list[dict]) -> list[list]:
    out = []
    for o in sorted(ops, key=lambda o: (o.get("timestamp_entry") or o.get("signal_timestamp") or "", int(o["id"]))):
        out.append([o["id"], "VL" if o["channel"] == "VICENTE_LUZ" else "GZ", o["ticker"], o["option_type"],
                    _d(o.get("signal_timestamp")), _d(o.get("timestamp_entry")), o.get("spot_entry", ""),
                    o.get("strike_usd", ""), o.get("price_entry", ""), _d(o.get("timestamp_exit")),
                    o.get("price_exit", ""), o.get("reason_exit") or o["estado"], o.get("days_held", ""),
                    td_pct(to_float(o.get("retorno_bruto_pct"))), o.get("comision_pct", ""),
                    td_pct(to_float(o.get("retorno_neto_pct")))])
    return out


OPS_HEADERS = ["#", "Canal", "Ticker", "Tipo", "Señal (UTC)", "Entrada (UTC)", "Spot entrada", "Strike",
               "Prima entrada", "Salida (UTC)", "Prima salida", "Regla / estado", "Días", "Bruto",
               "Comisión %", "Neto"]


def nota2_html(data: dict, cfg: dict) -> str:
    rows = []
    for ch, name in channels(cfg):
        n2 = summarize(ops_of(data, ch), cfg)["nota2"]
        rows.append([name, n2["tp_con_sombra"], n2["toco_100"], n2["toco_150"], n2["toco_200"]])
    return ("<h3>Análisis retrospectivo (§10 nota 2)</h3><p class='muted'>Operaciones cerradas por take-profit "
            "con seguimiento completo hasta el día 45: cuántas habrían tocado +100%, +150% o +200%. No cambia "
            "el resultado oficial.</p>" + table(["Canal", "Take-profit con 45 días vistos", "Tocó +100%",
                                                  "Tocó +150%", "Tocó +200%"], rows))


# --------------------------------------------------------------------------- niveles de ganancia
def strike_distance(option_type: str, spot, strike) -> float | None:
    """% del subyacente respecto del strike en la direccion de la operacion:
    >= 0 = ya paso el strike (en el dinero); < 0 = le faltaba ese % para llegar."""
    s, k = to_float(spot), to_float(strike)
    if not s or not k:
        return None
    return ((s - k) / k if option_type == "CALL" else (k - s) / k) * 100


def distance_label(d: float | None, near: float) -> str:
    if d is None:
        return "sin dato"
    if d >= 0:
        return "pasó el strike"
    return f"a menos de {near:g}% del strike" if d >= -near else f"a más de {near:g}% del strike"


def _event_status(o: dict, lv: int, ts: str) -> str:
    if o["estado"] != "closed":
        return "operación abierta"
    ex, reason = o.get("timestamp_exit") or "", o.get("reason_exit")
    if ts > ex or (ts == ex and reason == "take_profit" and lv > 50):
        label = {"take_profit": "take-profit", "stop_loss": "stop-loss", "expiry": "vencimiento"}.get(reason, reason)
        return f"en sombra: la operación ya había cerrado por {label}"
    if reason == "take_profit" and lv == 50:
        return "cierre oficial por take-profit (+50%)"
    return "antes del cierre"


def level_events(ops: list[dict]) -> list[dict]:
    """Un evento por operacion y nivel (+25/+50/+75/+100%) alcanzado por la prima."""
    out = []
    for o in ops:
        if o["estado"] not in ("open", "closed") or not o.get("timestamp_entry"):
            continue
        for lv in PROFIT_LEVELS:
            ts = o.get(f"nivel_{lv}_ts")
            if not ts:
                continue
            spot = to_float(o.get(f"nivel_{lv}_spot"))
            out.append({"op": o, "nivel": lv, "ts": ts, "spot": spot,
                        "dist_prot": strike_distance(o["option_type"], spot, o.get("strike_usd")),
                        "dist_vl": strike_distance(o["option_type"], spot, o.get("strike_vl")),
                        "dias": (parse_ts(ts) - parse_ts(o["timestamp_entry"])).total_seconds() / 86400,
                        "estado": _event_status(o, lv, ts)})
    return sorted(out, key=lambda e: (e["ts"], int(e["op"]["id"]), e["nivel"]))


def _pctcell(x) -> str:
    return "sin dato" if x is None else f"{x:+.1f}%"


def levels_html(data: dict, cfg: dict, a: str = "", b: str = "", period: str = "") -> str:
    """Seccion de niveles: eventos del periodo [a, b) y resumen acumulado por nivel."""
    near = float(cfg.get("strike_near_pct", 2))
    ops = ops_of(data, "VICENTE_LUZ")
    evs = level_events(ops)
    with_entry = [o for o in ops if o["estado"] in ("open", "closed") and o.get("timestamp_entry")]
    out = ["<p class='muted'>Ganancia de la prima (modelo) de cada operación en papel. La regla oficial sigue "
           "siendo cerrar en +50%; +75% y +100% se miden en sombra (§10 nota 2). Distancia al strike = precio del "
           f"subyacente en ese momento frente al strike: positiva si ya lo pasó; «cerca» = a menos de {near:g}%. "
           "Strike del protocolo = spot de entrada ±5%; strike de Vicente = el publicado en la señal.</p>"]
    if a or b:
        rows = [[e["op"]["id"], e["op"]["ticker"], e["op"]["option_type"], f"+{e['nivel']}%", e["ts"][:16],
                 f"{e['dias']:.1f}", e["spot"] if e["spot"] is not None else "sin dato",
                 e["op"].get("strike_usd", ""), _pctcell(e["dist_prot"]), e["op"].get("strike_vl") or "sin dato",
                 _pctcell(e["dist_vl"]), e["estado"]]
                for e in evs if (not a or e["ts"] >= a) and (not b or e["ts"] < b)]
        out.append(f"<h3>Niveles alcanzados {esc(period)}</h3>" + table(
            ["#", "Ticker", "Tipo", "Nivel", "Momento (UTC)", "Días desde entrada", "Precio subyacente",
             "Strike protocolo", "Distancia", "Strike Vicente", "Distancia", "Estado"], rows,
            "Ninguna operación llegó a un nuevo nivel en este período."))
    summ = []
    for lv in PROFIT_LEVELS:
        e_lv = [e for e in evs if e["nivel"] == lv]
        cat = lambda key, f: sum(1 for e in e_lv if f(e[key]))  # noqa: E731
        vl = [e for e in e_lv if e["dist_vl"] is not None]
        summ.append([f"+{lv}%", len(e_lv),
                     f"{len(e_lv) / len(with_entry) * 100:.0f}%" if with_entry else "sin dato",
                     cat("dist_prot", lambda d: d is not None and d >= 0),
                     cat("dist_prot", lambda d: d is not None and -near <= d < 0),
                     cat("dist_prot", lambda d: d is not None and d < -near),
                     f"{sum(e['dist_vl'] >= 0 for e in vl)} / {sum(-near <= e['dist_vl'] < 0 for e in vl)} / "
                     f"{sum(e['dist_vl'] < -near for e in vl)}" if vl else "sin dato"])
    out.append(f"<h3>Resumen acumulado ({len(with_entry)} operaciones con entrada)</h3>" + table(
        ["Nivel", "Operaciones que llegaron", "% del total", "Pasó el strike del protocolo",
         f"A menos de {near:g}%", f"Más lejos", "Strike de Vicente: pasó / cerca / lejos"], summ))
    return "".join(out)


def discard_rows(signals: list[dict]) -> list[list]:
    c = defaultdict(int)
    for s in signals:
        if s.get("estado") == "descartada":
            c[s.get("motivo") or "sin motivo"] += 1
    return [[MOTIVOS.get(k, k), v] for k, v in sorted(c.items(), key=lambda kv: -kv[1])]
