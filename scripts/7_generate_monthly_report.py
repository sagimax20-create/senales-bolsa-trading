"""Script 7 — Reporte mensual (PROTOCOLO §9: ultimo dia del mes; analisis detallado por
ticker, por canal y por tipo de opcion).

Que mes envia (fechas en hora de Nueva York):
  - El ultimo dia del mes: el mes en curso.
  - Cualquier mes anterior (desde capture_start) que no figure en data/report_state.json -> monthly_sent:
    se envia marcado "tardio". Asi un retraso de GitHub o un fallo no deja un mes sin reporte
    (el de octubre de 2026 nunca llego por un error del workflow).
  - --month AAAA-MM: ese mes, aunque ya se haya enviado.
Escribe reports/monthly_report_AAAA-MM.html y reports/report_monthly.html (el ultimo).
Credenciales: SMTP_HOST, SMTP_USER, SMTP_PASS, ALERT_EMAIL_TO.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common_utils import (NY, REPORT_STATE_PATH, REPORTS_DIR, get_config, load_state, log_event,  # noqa: E402
                          now_utc, send_email, update_state)
from report_lib import (channels, OPS_HEADERS, channel_signals, discard_rows, esc, group_table, levels_html,  # noqa: E402
                        kpis, load_data, milestones, milestones_html, nota2_html, ny_bounds, ops_rows,
                        page, pct, results_html, table, td_pct)
from signal_parser import PARSER_VERSION  # noqa: E402


def month_start(d: date) -> date:
    return d.replace(day=1)


def next_month(d: date) -> date:
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1)


def months_to_send(today: date, sent: set[str], capture_start: str, forced: str | None = None) -> list[tuple[str, bool]]:
    """[(AAAA-MM, tardio)] a enviar hoy."""
    if forced:
        return [(forced, False)]
    out = []
    m = month_start(date.fromisoformat(capture_start))
    current = month_start(today)
    while m < current:
        if m.strftime("%Y-%m") not in sent:
            out.append((m.strftime("%Y-%m"), True))
        m = next_month(m)
    if (today + timedelta(days=1)).month != today.month and current.strftime("%Y-%m") not in sent:
        out.append((current.strftime("%Y-%m"), False))
    return out


def _group_rows(groups: dict) -> list[list]:
    return [[k, v["n"], td_pct(v["avg"]), f"{v['win']:.0f}%"] for k, v in groups.items()]


def build_monthly(data: dict, cfg: dict, now: datetime, month: str, late: bool) -> tuple[str, str]:
    d0 = date.fromisoformat(month + "-01")
    d1 = next_month(d0)
    a, b = ny_bounds(d0, d1)
    counts = {ch: len(channel_signals(data, ch, a, b)) for ch, _ in channels(cfg)}
    vl_month = [s for s in data["signals"] if a <= s["timestamp"] < b]
    ops = data["ops"]
    opened = [o for o in ops if a <= (o.get("timestamp_entry") or "~") < b]
    closed_m = [o for o in ops if a <= (o.get("timestamp_exit") or "~") < b]
    name_of = dict(channels(cfg))
    alert = ""
    vl = milestones(data, "VICENTE_LUZ", cfg, now)["stats"]
    if vl["ret_sin_banco"] is not None and vl["ret_sin_banco"] < cfg["alert_return_threshold"] * 100:
        alert = (f"<div class='alert'>Vicente Luz: retorno neto sin costo bancario {esc(pct(vl['ret_sin_banco']))} "
                 f"(umbral de alerta {cfg['alert_return_threshold'] * 100:.0f}%)</div>")
    late_note = ("<p class='alert'>Reporte enviado con retraso: debía salir el último día del mes. "
                 "Las cifras están actualizadas a la fecha de generación.</p>") if late else ""
    body = (
        late_note + alert
        + f"<h2>Actividad de {esc(month)}</h2>"
        + kpis([("Señales válidas Vicente Luz", str(counts["VICENTE_LUZ"]))]
               + ([("Alertas Gravity Zone", str(counts["GRAVITY_ZONE"]))] if "GRAVITY_ZONE" in counts else [])
               + [("Operaciones abiertas en el mes", str(len(opened))),
                ("Operaciones cerradas en el mes", str(len(closed_m)))])
        + "<h3>Señales descartadas en el mes (Vicente Luz)</h3>"
        + table(["Motivo", "Señales"], discard_rows(vl_month), "Ninguna.")
        + "<h2>Avance hacia los hitos</h2>" + milestones_html(data, cfg, now)
        + "<h2>Resultados acumulados</h2>" + results_html(data, cfg)
        + "<h2>Análisis detallado (operaciones cerradas, acumulado)</h2>"
        + "<h3>Por canal</h3>" + table(["Canal", "Cerradas", "Neto prom.", "% ganadoras"],
                                       _group_rows(group_table(ops, lambda o: name_of.get(o["channel"], o["channel"]))),
                                       "Sin operaciones cerradas.")
        + "<h3>Por tipo de opción</h3>" + table(["Tipo", "Cerradas", "Neto prom.", "% ganadoras"],
                                                _group_rows(group_table(ops, lambda o: o["option_type"])),
                                                "Sin operaciones cerradas.")
        + "<h3>Por ticker</h3>" + table(["Ticker", "Cerradas", "Neto prom.", "% ganadoras"],
                                        _group_rows(group_table(ops, lambda o: o["ticker"])),
                                        "Sin operaciones cerradas.")
        + nota2_html(data, cfg)
        + "<h2>Niveles de ganancia: +25%, +50%, +75% y +100%</h2>"
        + levels_html(data, cfg, a, b, f"en {month}")
        + f"<h2>Operaciones abiertas o cerradas en {esc(month)}</h2>"
        + table(OPS_HEADERS, ops_rows(list({o["id"]: o for o in opened + closed_m}.values())), "Ninguna.")
    )
    title = f"Reporte mensual — Testeo de señales — {month}"
    html = page(title, f"Generado {now:%Y-%m-%d %H:%M} UTC" + (" · enviado con retraso" if late else ""), body)
    lines = [title + (" (enviado con retraso)" if late else "")]
    for ch, name in channels(cfg):
        m = milestones(data, ch, cfg, now)
        st = m["stats"]
        lines.append(f"{name}: {counts[ch]} señales en el mes, {m['n']} acumuladas; {st['n_closed']} cerradas; "
                     f"retorno neto con costo bancario {pct(st['ret_con_banco'])}")
    lines.append("Dinero ficticio. Esto es análisis de datos, no asesoría financiera.")
    return html, "\n".join(lines)


def main() -> int:
    cfg = get_config()
    state = load_state()
    if state.get("parser_version") != PARSER_VERSION:
        log_event("Script 7: las señales aun no se reprocesaron con el parser v2; no envio un reporte "
                  "con datos de la version 1", "ERROR")
        return 1
    now = now_utc()
    forced = None
    if "--month" in sys.argv:
        forced = sys.argv[sys.argv.index("--month") + 1].strip()
    elif os.getenv("REPORT_MONTH", "").strip():
        forced = os.getenv("REPORT_MONTH").strip()
    if forced:
        date.fromisoformat(forced + "-01")  # valida AAAA-MM
    sent = set(load_state(REPORT_STATE_PATH).get("monthly_sent", []))
    todo = months_to_send(now.astimezone(NY).date(), sent, cfg["capture_start"], forced)
    if not todo:
        log_event("Script 7: hoy no corresponde reporte mensual")
        return 0
    data = load_data(cfg)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    for month, late in todo:
        html, text = build_monthly(data, cfg, now, month, late)
        (REPORTS_DIR / f"monthly_report_{month}.html").write_text(html, encoding="utf-8")
        (REPORTS_DIR / "report_monthly.html").write_text(html, encoding="utf-8")
        if send_email(f"Reporte mensual — señales ({month})" + (" [tardío]" if late else ""), text, html):
            sent.add(month)
            update_state(REPORT_STATE_PATH, monthly_sent=sorted(sent))
        log_event(f"Script 7: reporte mensual {month} generado" + (" (tardio)" if late else ""))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log_event(f"Script 7 fallo: {type(e).__name__}: {e}", "ERROR")
        sys.exit(1)
