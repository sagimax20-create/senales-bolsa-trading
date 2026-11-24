"""Script 6 — Reporte semanal (PROTOCOLO §9: cada lunes; resumen de señales, retorno
acumulado y operaciones abiertas).

Cubre la ultima semana completa, de lunes a domingo en hora de Nueva York. Contenido:
  1. Señales validas acumuladas frente a los hitos 100 y 300, por canal.
  2. Señales de la semana: validas y descartadas por motivo.
  3. Operaciones abiertas, cerradas y pendientes.
  4. Resultado por regla de salida y retorno neto con comision y con costo bancario.
  5. Tabla de las operaciones de la semana (abiertas o cerradas en la semana).
  6. Operaciones abiertas hoy.
Escribe reports/weekly_report_<lunes del envio>.html y reports/report_weekly.html (el ultimo),
y lo envia por correo una sola vez por semana (data/report_state.json -> weekly_sent).
Opciones: --week AAAA-MM-DD (lunes de la semana a reportar), --force (reenviar).
Credenciales: SMTP_HOST, SMTP_USER, SMTP_PASS, ALERT_EMAIL_TO.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common_utils import (NY, REPORT_STATE_PATH, REPORTS_DIR, get_config, load_state, log_event,  # noqa: E402
                          now_utc, send_email, update_state)
from report_lib import (CHANNELS, OPS_HEADERS, channel_signals, discard_rows, kpis, load_data,  # noqa: E402
                        milestones, milestones_html, nota2_html, ny_bounds, ops_rows, page, pct,
                        results_html, summarize, table)
from signal_parser import PARSER_VERSION  # noqa: E402


def last_complete_week(now: datetime) -> tuple[date, date]:
    """(lunes, lunes siguiente) de la ultima semana completa en hora de Nueva York."""
    today = now.astimezone(NY).date()
    this_monday = today - timedelta(days=today.weekday())
    return this_monday - timedelta(days=7), this_monday


def build_weekly(data: dict, cfg: dict, now: datetime, d0: date, d1: date) -> tuple[str, str]:
    a, b = ny_bounds(d0, d1)
    week_counts = {ch: len(channel_signals(data, ch, a, b)) for ch, _ in CHANNELS}
    vl_week = [s for s in data["signals"] if a <= s["timestamp"] < b]
    msgs_week = [m for m in data["messages"] if a <= m["timestamp"] < b]
    ops = data["ops"]
    st_all = summarize(ops, cfg)
    week_ops = [o for o in ops if a <= (o.get("timestamp_entry") or "~") < b or a <= (o.get("timestamp_exit") or "~") < b]
    open_ops = [o for o in ops if o["estado"] == "open"]
    period = f"{d0:%d-%m-%Y} al {d1 - timedelta(days=1):%d-%m-%Y}"
    body = (
        f"<h2>Resumen de la semana del {period}</h2>"
        + kpis([("Señales válidas Vicente Luz (semana)", str(week_counts["VICENTE_LUZ"])),
                ("Alertas Gravity Zone (semana)", str(week_counts["GRAVITY_ZONE"])),
                ("Mensajes leídos (semana)", str(len(msgs_week))),
                ("Operaciones abiertas", str(st_all["n_open"])),
                ("Operaciones cerradas (acumulado)", str(st_all["n_closed"])),
                ("Pendientes / sin precio", f"{st_all['n_pending']} / {st_all['n_sin_precio']}")])
        + "<h2>Avance hacia los hitos</h2>" + milestones_html(data, cfg, now, week_counts)
        + "<h2>Señales descartadas esta semana (Vicente Luz)</h2>"
        + table(["Motivo", "Señales"], discard_rows(vl_week), "Ninguna.")
        + "<p class='muted'>Además de estas, los mensajes sin ticker válido o sin call/put no generan señal "
          "(ver data/messages.csv).</p>"
        + "<h2>Resultados</h2>" + results_html(data, cfg)
        + f"<h2>Operaciones de la semana ({len(week_ops)})</h2>"
        + table(OPS_HEADERS, ops_rows(week_ops), "Ninguna operación abrió ni cerró esta semana.")
        + f"<h2>Operaciones abiertas hoy ({len(open_ops)})</h2>"
        + "<p class='muted'>El retorno de una operación abierta es provisional (marcado a mercado con el modelo).</p>"
        + table(OPS_HEADERS, ops_rows(open_ops), "No hay operaciones abiertas.")
        + nota2_html(data, cfg)
    )
    title = "Reporte semanal — Testeo de señales"
    html = page(title, f"Semana del {period} · generado {now:%Y-%m-%d %H:%M} UTC", body)
    lines = [f"{title} — semana del {period}"]
    for ch, name in CHANNELS:
        m = milestones(data, ch, cfg, now)
        st = m["stats"]
        lines.append(f"{name}: {week_counts[ch]} señales esta semana, {m['n']} acumuladas "
                     f"(alarma {m['n']}/{m['alarm']}, decisión {m['n']}/{m['final']}); "
                     f"{st['n_closed']} cerradas, {st['n_open']} abiertas; retorno neto con costo bancario "
                     f"{pct(st['ret_con_banco'])}")
    lines.append(f"Operaciones de la semana: {len(week_ops)}. Detalle en la versión HTML.")
    lines.append("Dinero ficticio. Esto es análisis de datos, no asesoría financiera.")
    return html, "\n".join(lines)


def main() -> int:
    cfg = get_config()
    state = load_state()
    if state.get("parser_version") != PARSER_VERSION:
        log_event("Script 6: las señales aun no se reprocesaron con el parser v2; no envio un reporte "
                  "con datos de la version 1", "ERROR")
        return 1
    now = now_utc()
    d0, d1 = last_complete_week(now)
    if "--week" in sys.argv:
        d0 = date.fromisoformat(sys.argv[sys.argv.index("--week") + 1])
        d0 -= timedelta(days=d0.weekday())
        d1 = d0 + timedelta(days=7)
    week_id = d0.isoformat()
    sent = set(load_state(REPORT_STATE_PATH).get("weekly_sent", []))
    if week_id in sent and "--force" not in sys.argv:
        log_event(f"Script 6: semana {week_id} ya enviada; nada que hacer")
        return 0
    html, text = build_weekly(load_data(), cfg, now, d0, d1)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / f"weekly_report_{d1.isoformat()}.html").write_text(html, encoding="utf-8")
    (REPORTS_DIR / "report_weekly.html").write_text(html, encoding="utf-8")
    subject = f"Reporte semanal — señales ({d0:%d-%m} al {d1 - timedelta(days=1):%d-%m-%Y})"
    if send_email(subject, text, html):
        update_state(REPORT_STATE_PATH, weekly_sent=sorted(sent | {week_id}))
    log_event(f"Script 6: reporte semanal {week_id} generado")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log_event(f"Script 6 fallo: {type(e).__name__}: {e}", "ERROR")
        sys.exit(1)
