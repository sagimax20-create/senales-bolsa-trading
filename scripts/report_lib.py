"""Calculos y HTML compartidos por los reportes (scripts 5, 6 y 7).

Retorno neto (PROTOCOLO §6), sobre operaciones CERRADAS de un canal:
  capital operado  = n_cerradas * $1,000                       ($1,000 por señal, §2)
  p&l neto         = suma(1,000 * retorno_neto_i)               (ya descuenta la comision, §4)
  costo bancario   = como el ejemplo del §4: se deposita capital_operado / 0.92 (el banco
                     descuenta 8% y se opera con el 92%) y al retirar el banco descuenta 10%
  retorno neto     = (lo que se recibe - lo depositado) / lo depositado
  Ejemplo §4: deposito $10,000 -> opera $9,200 -> retira $11,200 -> recibe $10,080 -> +0.8%.
Hitos (PROTOCOLO §5) por canal: Vicente Luz cuenta señales validas; Gravity Zone, alertas.
"""
from __future__ import annotations

import html
from collections import defaultdict
from datetime import date, datetime, timedelta

from common_utils import (GRAVITY_COLS, GRAVITY_CSV, MESSAGES_COLS, MESSAGES_CSV, NY,
                          OPERATIONS_CSV, OPS_COLS, SIGNALS_COLS, SIGNALS_CSV, fmt_ts, load_csv,
                          now_utc, parse_ts, to_float)

CHANNELS = [("VICENTE_LUZ", "Vicente Luz"), ("GRAVITY_ZONE", "Gravity Zone")]
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
    "vela por vela. Gravity Zone es una aproximación (z-score diario), no el indicador original. "
    "Dinero ficticio. Esto es análisis de datos, no asesoría financiera."
)


# --------------------------------------------------------------------------- datos
def load_data() -> dict:
    signals = load_csv(SIGNALS_CSV, SIGNALS_COLS)
    return {
        "signals": signals,
        "valid": [s for s in signals if s.get("estado") == "valida"],
        "gravity": load_csv(GRAVITY_CSV, GRAVITY_COLS),
        "ops": load_csv(OPERATIONS_CSV, OPS_COLS),
        "messages": load_csv(MESSAGES_CSV, MESSAGES_COLS),
    }


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
    for ch, name in CHANNELS:
        m = milestones(data, ch, cfg, now)
        extra = f" · esta semana: {week_counts[ch]}" if week_counts else ""
        out.append(f"<h3>{esc(name)}</h3><p>Señales válidas acumuladas: <b>{m['n']}</b>{esc(extra)}</p>"
                   f"{progress('Alarma', m['n'], m['alarm'])}<p class='muted'>Fecha estimada: {esc(m['eta_alarm'])}"
                   f" · {esc(m['a_action'])}</p>{progress('Decisión final', m['n'], m['final'])}"
                   f"<p class='muted'>Fecha estimada: {esc(m['eta_final'])} · {esc(m['f_state'])}. {esc(m['f_action'])}</p>")
    return "".join(out)


def results_html(data: dict, cfg: dict) -> str:
    rows, reason_rows = [], []
    views = [(name, ops_of(data, ch)) for ch, name in CHANNELS]
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
    for ch, name in CHANNELS:
        n2 = summarize(ops_of(data, ch), cfg)["nota2"]
        rows.append([name, n2["tp_con_sombra"], n2["toco_100"], n2["toco_150"], n2["toco_200"]])
    return ("<h3>Análisis retrospectivo (§10 nota 2)</h3><p class='muted'>Operaciones cerradas por take-profit "
            "con seguimiento completo hasta el día 45: cuántas habrían tocado +100%, +150% o +200%. No cambia "
            "el resultado oficial.</p>" + table(["Canal", "Take-profit con 45 días vistos", "Tocó +100%",
                                                  "Tocó +150%", "Tocó +200%"], rows))


def discard_rows(signals: list[dict]) -> list[list]:
    c = defaultdict(int)
    for s in signals:
        if s.get("estado") == "descartada":
            c[s.get("motivo") or "sin motivo"] += 1
    return [[MOTIVOS.get(k, k), v] for k, v in sorted(c.items(), key=lambda kv: -kv[1])]
