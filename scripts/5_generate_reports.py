"""Script 5 — Hitos de decision (reports/hitos.md) y avisos de alarma / decision final.

Corre en cada pasada del monitor. Hitos por canal (PROTOCOLO §5, §1 "paralelo"):
  Vicente Luz  : señales validas (parser v2, universo ampliado por ADENDA-01)
  Gravity Zone : alertas (8 tickers originales)
Avisos por correo (PROTOCOLO §9 "En alarma" y "Final"), una sola vez por canal y evento:
  alarma (100 señales), decision (300 señales) y veredicto (cuando cierran las 300 operaciones).
Los reportes semanal y mensual los generan los scripts 6 y 7. `--force` reescribe hitos.md.
Calculos: scripts/report_lib.py.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common_utils import REPORTS_DIR, get_config, load_state, log_event, now_utc, send_email, update_state  # noqa: E402
from report_lib import CHANNELS, load_data, milestones, pct  # noqa: E402
from signal_parser import PARSER_VERSION  # noqa: E402


def build_hitos(data: dict, cfg: dict, now) -> str:
    out = ["# Hitos de Decisión\n",
           f"_Actualizado: {now:%Y-%m-%d} UTC · generado por scripts/5_generate_reports.py · "
           "hitos por canal (PROTOCOLO §5); universo de Vicente Luz según docs/ADENDA-01-UNIVERSO.md_\n"]
    for ch, name in CHANNELS:
        m = milestones(data, ch, cfg, now)
        st = m["stats"]
        out.append(
            f"## {name}\n"
            f"- Señales válidas: {m['n']}\n"
            f"- Alarma ({m['alarm']}): {m['a_state']} · fecha estimada: {m['eta_alarm']}\n"
            f"  - Retorno neto sin costo bancario: {pct(st['ret_sin_banco'])} · Acción: {m['a_action']}\n"
            f"- Decisión final ({m['final']}): {m['f_state']} · fecha estimada: {m['eta_final']}\n"
            f"  - Retorno neto con costo bancario (criterio §6): {pct(st['ret_con_banco'])} · Acción: {m['f_action']}\n"
            f"- Operaciones: {st['n_closed']} cerradas · {st['n_open']} abiertas · {st['n_pending']} pendientes"
            f" · {st['n_sin_precio']} sin precio\n")
    return "\n".join(out)


def write_if_changed(path, text: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8").split("\n", 3)[3:] == text.split("\n", 3)[3:]:
        return False  # mismo contenido salvo la fecha de actualizacion
    path.write_text(text, encoding="utf-8")
    return True


def main() -> int:
    cfg = get_config()
    state = load_state()
    if state.get("parser_version") != PARSER_VERSION:
        log_event("Script 5: esperando el reprocesamiento de señales (parser v2); hitos sin cambios")
        return 0
    now = now_utc()
    data = load_data()
    text = build_hitos(data, cfg, now)
    path = REPORTS_DIR / "hitos.md"
    written = write_if_changed(path, text)
    if not written and "--force" in sys.argv:
        path.write_text(text, encoding="utf-8")
        written = True
    notified = set(state.get("hitos_notified", []))
    counts = {}
    for ch, name in CHANNELS:
        m = milestones(data, ch, cfg, now)
        counts[name] = m["n"]
        events = [("alarma", m["n"] >= m["alarm"], f"Alarma alcanzada: {name} ({m['alarm']} señales)"),
                  ("decision", m["n"] >= m["final"], f"Decisión final alcanzada: {name} ({m['final']} señales)"),
                  ("veredicto", m["verdict"] is not None, f"Veredicto {name}: {m['verdict']}")]
        for key, reached, subject in events:
            tag = f"{ch}:{key}"
            if reached and tag not in notified and send_email(subject, text):
                notified.add(tag)
    if notified != set(state.get("hitos_notified", [])):
        update_state(hitos_notified=sorted(notified))
    log_event(f"Script 5: hitos.md {'actualizado' if written else 'sin cambios'}; "
              + "; ".join(f"{name}: {n} señales" for name, n in counts.items()))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log_event(f"Script 5 fallo: {type(e).__name__}: {e}", "ERROR")
        sys.exit(1)
