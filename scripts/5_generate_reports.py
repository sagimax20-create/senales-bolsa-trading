"""Script 5 — Reportes (HTML semanal, HTML mensual, hitos.md).

- hitos.md            : se reescribe en cada corrida, solo si cambio el contenido.
- report_weekly.html  : los lunes (UTC) o si no existe.
- report_monthly.html : el ultimo dia del mes (UTC) o si no existe.
- `--force` regenera todo.

Definicion de retorno (PROTOCOLO §6), calculada sobre operaciones CERRADAS:
  capital      = n_cerradas * $1,000
  p&l neto     = suma(capital_i * retorno_neto_i)      (ya descuenta comisiones broker)
  costo banco  = 8% * capital  +  10% * max(capital + p&l neto, 0)     (18% de ciclo)
  retorno neto = (p&l neto - costo banco) / capital
Tambien se muestra el retorno SIN costo bancario, porque ese costo solo se paga una vez por
ciclo y domina las primeras cifras; el criterio de rechazo del protocolo usa el CON banco.
"""
from __future__ import annotations

import html
import os
import sys
from collections import defaultdict
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common_utils import (OPERATIONS_CSV, OPS_COLS, REPORTS_DIR, get_config, load_csv, load_state,  # noqa: E402
                          log_event, now_utc, parse_ts, save_state, send_email_alert, to_float)


# --------------------------------------------------------------------------- estadisticas
def group_stats(ops: list[dict], keyfn) -> dict:
    g = defaultdict(list)
    for o in ops:
        g[keyfn(o)].append(to_float(o["retorno_neto_pct"], 0.0))
    return {k: {"n": len(v), "avg": sum(v) / len(v), "win": sum(x > 0 for x in v) / len(v) * 100}
            for k, v in sorted(g.items())}


def compute_stats(ops: list[dict], cfg: dict) -> dict:
    cap = float(cfg["protocol_capital_per_signal"])
    closed = [o for o in ops if o["estado"] == "closed"]
    openn = [o for o in ops if o["estado"] == "open"]
    pending = [o for o in ops if o["estado"] == "pending"]
    nets = [to_float(o["retorno_neto_pct"], 0.0) for o in closed]
    capital = len(closed) * cap
    pnl = sum(cap * x / 100 for x in nets)
    bank = cfg["protocol_bank_cost_entry"] * capital + cfg["protocol_bank_cost_exit"] * max(capital + pnl, 0)
    return {
        "n_total": len(ops), "n_closed": len(closed), "n_open": len(openn), "n_pending": len(pending),
        "avg_net": sum(nets) / len(nets) if nets else None,
        "win_rate": sum(x > 0 for x in nets) / len(nets) * 100 if nets else None,
        "ret_sin_banco": pnl / capital * 100 if capital else None,
        "ret_con_banco": (pnl - bank) / capital * 100 if capital else None,
        "pnl_usd": pnl, "bank_usd": bank, "capital": capital,
        "by_ticker": group_stats(closed, lambda o: o["ticker"]),
        "by_channel": group_stats(closed, lambda o: o["channel"]),
        "by_type": group_stats(closed, lambda o: o["option_type"]),
        "by_reason": {r: sum(o["reason_exit"] == r for o in closed) for r in ("take_profit", "stop_loss", "expiry")},
        "closed": sorted(closed, key=lambda o: o["timestamp_exit"]),
        "first_signal": min((o["signal_timestamp"] for o in ops if o["signal_timestamp"]), default=None),
        "cap": cap,
    }


def pct(x, nd=1, plus=True):
    return "n/d" if x is None else f"{x:+.{nd}f}%" if plus else f"{x:.{nd}f}%"


def eta(st: dict, target: int) -> str:
    if st["n_total"] >= target:
        return "alcanzado"
    if not st["first_signal"] or st["n_total"] < 5:
        return "n/d (pocas señales para proyectar)"
    days = max((now_utc() - parse_ts(st["first_signal"])).total_seconds() / 86400, 1)
    rate = st["n_total"] / days
    return (now_utc() + timedelta(days=(target - st["n_total"]) / rate)).strftime("%Y-%m-%d") + " (proyeccion)"


# --------------------------------------------------------------------------- hitos.md
def build_hitos(st: dict, cfg: dict) -> str:
    alarm, final = cfg["protocol_hito_alarm"], cfg["protocol_hito_decision"]
    thr = cfg["alert_return_threshold"] * 100
    n = st["n_total"]
    if n < alarm:
        a_state, a_action = f"EN PROGRESO ({n}/{alarm})", "Seguir capturando."
    elif st["ret_sin_banco"] is None:
        a_state, a_action = f"ALCANZADA ({n}/{alarm})", "Aun no hay operaciones cerradas para evaluar."
    elif st["ret_sin_banco"] < thr:
        a_state, a_action = f"ALCANZADA ({n}/{alarm})", f"REVISAR PROTOCOLO: retorno sin banco < {thr:.0f}%."
    else:
        a_state, a_action = f"ALCANZADA ({n}/{alarm})", f"CONTINUAR: retorno sin banco >= {thr:.0f}%."
    if n < final:
        f_state, f_action = f"PENDIENTE ({n}/{final})", f"Si el retorno neto < {cfg['protocol_min_return']*100:.0f}% se descarta."
    else:
        ok = st["ret_con_banco"] is not None and st["ret_con_banco"] >= cfg["protocol_min_return"] * 100
        f_state = f"ALCANZADA ({n}/{final}); {st['n_open']} abiertas y {st['n_pending']} pendientes sin cerrar"
        f_action = ("VALIDADA (candidata a dinero real)" if ok else "DESCARTADA") + \
                   f" con retorno neto {pct(st['ret_con_banco'])}"
    return (
        "# Hitos de Decisión\n\n"
        f"_Actualizado: {now_utc():%Y-%m-%d} UTC · generado por scripts/5_generate_reports.py_\n\n"
        f"## Alarma ({alarm} señales)\n"
        f"- Estado: {a_state}\n- Fecha estimada: {eta(st, alarm)}\n"
        f"- Retorno neto sin costo bancario: {pct(st['ret_sin_banco'])}\n- Acción: {a_action}\n\n"
        f"## Decisión Final ({final} señales)\n"
        f"- Estado: {f_state}\n- Fecha estimada: {eta(st, final)}\n"
        f"- Retorno neto con costo bancario (criterio del protocolo): {pct(st['ret_con_banco'])}\n"
        f"- Acción: {f_action}\n\n"
        f"Operaciones: {st['n_closed']} cerradas · {st['n_open']} abiertas · {st['n_pending']} pendientes.\n"
    )


# --------------------------------------------------------------------------- HTML
CSS = """
body{margin:0;background:#fff;color:#0B1F3A;font-family:'Arial Narrow',Arial,sans-serif;line-height:1.45}
header{background:#0B1F3A;color:#fff;padding:22px 28px;border-bottom:4px solid #C9A227}
header h1{margin:0;font-size:24px;letter-spacing:.3px}header p{margin:4px 0 0;color:#E8D48A}
main{max-width:980px;margin:0 auto;padding:20px 28px 40px}
h2{color:#0B1F3A;border-bottom:2px solid #C9A227;padding-bottom:4px;margin-top:30px}
.kpis{display:flex;flex-wrap:wrap;gap:12px}.kpi{flex:1 1 150px;border:1px solid #0B1F3A;border-top:4px solid #C9A227;padding:10px 14px}
.kpi b{display:block;font-size:22px}.kpi span{font-size:13px;color:#4a5a73}
table{border-collapse:collapse;width:100%;margin-top:8px}th{background:#0B1F3A;color:#fff;text-align:left;padding:6px 10px}
td{padding:6px 10px;border-bottom:1px solid #d9dee6}.neg{color:#B3261E}.pos{color:#1B6E3C}
.alert{background:#B3261E;color:#fff;padding:12px 16px;margin-top:18px;font-weight:bold}
.note{font-size:13px;color:#4a5a73;margin-top:26px;border-top:1px solid #d9dee6;padding-top:10px}
svg{width:100%;height:auto;border:1px solid #d9dee6}
"""

SUPUESTOS = (
    "Supuestos del modelo: las primas son teóricas (Black-Scholes con volatilidad realizada 20d como IV, "
    "tasa libre de riesgo en config.json) porque el pipeline no consulta cotizaciones reales de opciones; "
    "los cruces de +50%/−50% se detectan con las capturas de precio cada 15 min; la señal de Gravity Zone "
    "es un z-score diario de aproximación, no el indicador original. Dinero ficticio — no es asesoría de inversión."
)


def cls(x):
    return "" if x is None else ("pos" if x > 0 else "neg" if x < 0 else "")


def table(title: str, groups: dict) -> str:
    if not groups:
        return f"<h3>{html.escape(title)}</h3><p>Sin operaciones cerradas aún.</p>"
    rows = "".join(
        f"<tr><td>{html.escape(str(k))}</td><td>{v['n']}</td><td class='{cls(v['avg'])}'>{pct(v['avg'])}</td>"
        f"<td>{v['win']:.0f}%</td></tr>" for k, v in groups.items())
    return (f"<h3>{html.escape(title)}</h3><table><tr><th>{html.escape(title)}</th><th>Cerradas</th>"
            f"<th>Neto prom.</th><th>% ganadoras</th></tr>{rows}</table>")


def cumulative_svg(st: dict) -> str:
    pts, acc = [], 0.0
    for o in st["closed"]:
        acc += st["cap"] * to_float(o["retorno_neto_pct"], 0.0) / 100
        pts.append(acc)
    if len(pts) < 2:
        return "<p>El gráfico aparece con 2 o más operaciones cerradas.</p>"
    w, h, pad = 900, 240, 30
    lo, hi = min(0, min(pts)), max(0, max(pts))
    span = (hi - lo) or 1
    xy = [(pad + i * (w - 2 * pad) / (len(pts) - 1), h - pad - (v - lo) / span * (h - 2 * pad)) for i, v in enumerate(pts)]
    zero = h - pad - (0 - lo) / span * (h - 2 * pad)
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in xy)
    return (f"<svg viewBox='0 0 {w} {h}'><line x1='{pad}' x2='{w-pad}' y1='{zero:.1f}' y2='{zero:.1f}' "
            f"stroke='#C9A227' stroke-dasharray='4'/><polyline fill='none' stroke='#0B1F3A' stroke-width='2.5' "
            f"points='{line}'/><text x='{pad}' y='16' font-size='12' fill='#0B1F3A'>P&amp;L neto acumulado (USD, sin banco): "
            f"{pts[-1]:+,.0f}</text></svg>")


def kpis(st: dict) -> str:
    items = [("Señales totales", str(st["n_total"])), ("Cerradas", str(st["n_closed"])),
             ("Abiertas / pendientes", f"{st['n_open']} / {st['n_pending']}"),
             ("Neto promedio por señal", pct(st["avg_net"])), ("% ganadoras", pct(st["win_rate"], 0, False)),
             ("Retorno sin banco", pct(st["ret_sin_banco"])), ("Retorno con banco", pct(st["ret_con_banco"]))]
    return "<div class='kpis'>" + "".join(f"<div class='kpi'><b>{html.escape(v)}</b><span>{html.escape(k)}</span></div>"
                                          for k, v in items) + "</div>"


def best_worst(st: dict) -> str:
    bt = st["by_ticker"]
    if not bt:
        return ""
    b = max(bt.items(), key=lambda kv: kv[1]["avg"])
    w = min(bt.items(), key=lambda kv: kv[1]["avg"])
    return f"<p>Mejor ticker: <b>{b[0]}</b> ({pct(b[1]['avg'])}) · Peor ticker: <b>{w[0]}</b> ({pct(w[1]['avg'])})</p>"


def page(title: str, subtitle: str, body: str) -> str:
    return (f"<!doctype html><html lang='es'><head><meta charset='utf-8'><meta name='viewport' "
            f"content='width=device-width,initial-scale=1'><title>{html.escape(title)}</title><style>{CSS}</style></head>"
            f"<body><header><h1>{html.escape(title)}</h1><p>{html.escape(subtitle)}</p></header><main>{body}"
            f"<p class='note'>{html.escape(SUPUESTOS)}</p></main></body></html>")


def build_weekly(st: dict, cfg: dict) -> str:
    final = cfg["protocol_hito_decision"]
    body = (f"<h2>Resumen Vicente Luz + Gravity Zone</h2>{kpis(st)}{best_worst(st)}"
            f"<p>Salidas: take-profit {st['by_reason']['take_profit']} · stop-loss {st['by_reason']['stop_loss']} · "
            f"vencimiento {st['by_reason']['expiry']}</p>"
            f"<p>Próximo hito: {cfg['protocol_hito_alarm']} señales "
            f"({max(cfg['protocol_hito_alarm'] - st['n_total'], 0)} faltantes) · decisión final: {final} "
            f"({max(final - st['n_total'], 0)} faltantes)</p><h2>Retorno acumulado</h2>{cumulative_svg(st)}")
    return page("Reporte semanal — Testeo de señales", f"Generado {now_utc():%Y-%m-%d %H:%M} UTC", body)


def build_monthly(st: dict, cfg: dict) -> str:
    alert = ""
    if st["ret_sin_banco"] is not None and st["ret_sin_banco"] < cfg["alert_return_threshold"] * 100:
        alert = (f"<div class='alert'>ALERTA: retorno neto sin banco {pct(st['ret_sin_banco'])} "
                 f"(umbral {cfg['alert_return_threshold']*100:.0f}%)</div>")
    body = (f"{alert}<h2>Análisis mensual detallado</h2>{kpis(st)}{best_worst(st)}"
            f"{table('Canal', st['by_channel'])}{table('Tipo', st['by_type'])}{table('Ticker', st['by_ticker'])}"
            f"<h2>Retorno acumulado</h2>{cumulative_svg(st)}")
    return page("Reporte mensual — Testeo de señales", f"Generado {now_utc():%Y-%m-%d %H:%M} UTC", body)


def write_if_changed(path, text: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.write_text(text, encoding="utf-8")
    return True


def main() -> int:
    cfg = get_config()
    force = "--force" in sys.argv
    ops = load_csv(OPERATIONS_CSV, OPS_COLS)
    st = compute_stats(ops, cfg)
    now = now_utc()
    written = []

    if write_if_changed(REPORTS_DIR / "hitos.md", build_hitos(st, cfg)):
        written.append("hitos.md")
    weekly, monthly = REPORTS_DIR / "report_weekly.html", REPORTS_DIR / "report_monthly.html"
    if force or now.weekday() == 0 or not weekly.exists():
        # El HTML lleva hora de generacion: solo se reescribe por motivo (lunes/forzado/inexistente)
        weekly.write_text(build_weekly(st, cfg), encoding="utf-8")
        written.append(weekly.name)
    if force or (now + timedelta(days=1)).month != now.month or not monthly.exists():
        monthly.write_text(build_monthly(st, cfg), encoding="utf-8")
        written.append(monthly.name)

    # Aviso por correo (opcional) una sola vez por hito
    state = load_state()
    notified = set(state.get("hitos_notified", []))
    for name, target in (("alarma", cfg["protocol_hito_alarm"]), ("decision", cfg["protocol_hito_decision"])):
        if st["n_total"] >= target and name not in notified:
            send_email_alert(f"Hito alcanzado: {name} ({target} señales)", build_hitos(st, cfg))
            notified.add(name)
    if notified != set(state.get("hitos_notified", [])):
        state["hitos_notified"] = sorted(notified)
        save_state(state)

    log_event(f"Script 5: escritos {written or 'nada (sin cambios)'}; {st['n_total']} señales, "
              f"{st['n_closed']} cerradas")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log_event(f"Script 5 fallo: {type(e).__name__}: {e}", "ERROR")
        sys.exit(1)
