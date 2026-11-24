"""Respuestas del bot de Telegram a los comandos (funciones puras: datos -> texto).

Usan las mismas cuentas que los reportes (scripts/report_lib.py). El texto es plano (sin
Markdown) y cada respuesta cabe en un mensaje de Telegram. Dinero ficticio: analisis de datos,
no asesoria financiera.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from ops_engine import PROFIT_LEVELS
from report_lib import (MOTIVOS, distance_label, level_events, milestones, ops_of, pct, strike_distance,
                        summarize, usd)
from common_utils import to_float

MAX_CHARS = 3900
DISCLAIMER = "Dinero ficticio; análisis de datos, no asesoría financiera."

HELP = (
    "Comandos disponibles:\n"
    "/estado – resumen: señales, avance hacia 100 y 300, retorno neto\n"
    "/abiertas – operaciones abiertas: retorno actual, nivel máximo y distancia al strike\n"
    "/niveles – últimos niveles +25/+50/+75/+100% alcanzados\n"
    "/senales – últimas señales de Vicente Luz y descartes de la semana\n"
    "/aciertos – tickers con más aciertos (take-profit y llegada al strike)\n"
    "/ayuda – esta lista\n\n" + DISCLAIMER
)


def _money(x) -> str:
    v = to_float(x)
    return "sin dato" if v is None else f"${v:,.2f}"


def _d(ts: str) -> str:
    return ts[5:16] if ts else "sin dato"   # "MM-DD HH:MM"


def _clip(text: str) -> str:
    return text if len(text) <= MAX_CHARS else text[:MAX_CHARS - 40] + "\n… (lista recortada; ver reporte)"


def _max_level(o: dict) -> int | None:
    reached = [lv for lv in PROFIT_LEVELS if o.get(f"nivel_{lv}_ts")]
    return max(reached) if reached else None


def cmd_estado(data: dict, cfg: dict, now: datetime, state: dict) -> str:
    m = milestones(data, "VICENTE_LUZ", cfg, now)
    st = m["stats"]
    tp, sl = st["by_reason"]["take_profit"]["n"], st["by_reason"]["stop_loss"]["n"]
    lines = [
        "📊 Vicente Luz — estado del testeo en papel",
        f"Señales válidas: {m['n']}",
        f"Alarma ({m['alarm']}): {m['a_state']}",
        f"Decisión final ({m['final']}): {m['f_state']} · estimada: {m['eta_final']}",
        f"Operaciones: {st['n_closed']} cerradas (take-profit {tp}, stop-loss {sl}) · {st['n_open']} abiertas"
        f" · {st['n_pending'] + st['n_sin_precio']} pendientes",
        f"% ganadoras: {pct(st['win_rate'], 0, False)} · neto promedio por operación: {pct(st['avg_net'])}",
        f"P&L neto: {usd(st['pnl_usd'])} · comisiones: {usd(st['comisiones_usd'])}",
        f"Retorno sin costo bancario: {pct(st['ret_sin_banco'])}",
        f"Retorno neto con costo bancario (criterio §6): {pct(st['ret_con_banco'])}",
        f"Acción en la alarma: {m['a_action']}",
        f"Datos al {state.get('prices_last_fetch', 'sin dato')} UTC.",
        DISCLAIMER,
    ]
    return "\n".join(lines)


def cmd_abiertas(data: dict, cfg: dict) -> str:
    near = float(cfg.get("strike_near_pct", 2))
    ops = sorted((o for o in ops_of(data, "VICENTE_LUZ") if o["estado"] == "open"),
                 key=lambda o: o.get("timestamp_entry") or "")
    if not ops:
        return "No hay operaciones abiertas.\n" + DISCLAIMER
    lines = [f"📂 Operaciones abiertas ({len(ops)})"]
    for o in ops:
        lv = _max_level(o)
        d = strike_distance(o["option_type"], o.get("spot_favorable_45d"), o.get("strike_usd"))
        lines.append(
            f"#{o['id']} {o['ticker']} {o['option_type']} · entrada {_d(o.get('timestamp_entry'))} a "
            f"{_money(o.get('spot_entry'))} · strike {_money(o.get('strike_usd'))}\n"
            f"   retorno actual (modelo): {pct(to_float(o.get('retorno_neto_pct')))} neto · "
            f"nivel máximo: {('+' + str(lv) + '%') if lv else 'ninguno'} · "
            f"mejor precio: {_money(o.get('spot_favorable_45d'))} ({distance_label(d, near)}) · "
            f"{o.get('days_held') or '0'} días")
    lines.append(DISCLAIMER)
    return _clip("\n".join(lines))


def cmd_niveles(data: dict, cfg: dict, n: int = 10) -> str:
    near = float(cfg.get("strike_near_pct", 2))
    evs = level_events(ops_of(data, "VICENTE_LUZ"))[-n:][::-1]
    if not evs:
        return "Todavía no hay niveles alcanzados.\n" + DISCLAIMER
    lines = [f"🎯 Últimos {len(evs)} niveles alcanzados (prima, modelo)"]
    for e in evs:
        o = e["op"]
        vl = (f" · Vicente {e['dist_vl']:+.1f}% ({distance_label(e['dist_vl'], near)})"
              if e["dist_vl"] is not None else "")
        lines.append(
            f"{_d(e['ts'])} UTC · {o['ticker']} {o['option_type']} +{e['nivel']}% · subyacente {_money(e['spot'])}\n"
            f"   strike protocolo "
            + ("sin dato" if e["dist_prot"] is None else f"{e['dist_prot']:+.1f}% ({distance_label(e['dist_prot'], near)})")
            + vl + f" · {e['estado']}")
    lines.append(DISCLAIMER)
    return _clip("\n".join(lines))


def cmd_senales(data: dict, cfg: dict, now: datetime, n: int = 10) -> str:
    valid = sorted(data["valid"], key=lambda s: s["timestamp"])[-n:][::-1]
    lines = [f"📨 Últimas {len(valid)} señales válidas de Vicente Luz"]
    for s in valid:
        extra = " · ".join(x for x in (
            f"strike {s['strike_vl']}" if s.get("strike_vl") else "",
            f"vence {s['expiry_vl']}" if s.get("expiry_vl") else "",
            f"prima {s['price_recommended']}" if s.get("price_recommended") else "",
            f"spread {s['spread']}" if s.get("spread") else "") if x)
        lines.append(f"{_d(s['timestamp'])} UTC · {s['ticker']} {s['signal_type']}" + (f" · {extra}" if extra else ""))
    week_ago = now.timestamp() - 7 * 86400
    disc = defaultdict(int)
    for s in data["signals"]:
        if s.get("estado") == "descartada":
            try:
                ts = datetime.strptime(s["timestamp"], "%Y-%m-%d %H:%M:%S").timestamp()
            except ValueError:
                continue
            if ts >= week_ago:
                disc[s.get("motivo") or "sin motivo"] += 1
    if disc:
        lines.append("Descartadas en los últimos 7 días: " + "; ".join(
            f"{MOTIVOS.get(k, k)}: {v}" for k, v in sorted(disc.items(), key=lambda kv: -kv[1])))
    lines.append(DISCLAIMER)
    return _clip("\n".join(lines))


def cmd_aciertos(data: dict, cfg: dict, min_ops: int = 2, top: int = 10) -> str:
    """Ranking por ticker: % de operaciones cerradas por take-profit y % que llego al strike."""
    groups = defaultdict(list)
    for o in ops_of(data, "VICENTE_LUZ"):
        if o["estado"] == "closed":
            groups[o["ticker"]].append(o)
    rows = []
    for t, ops in groups.items():
        if len(ops) < min_ops:
            continue
        tp = sum(o["reason_exit"] == "take_profit" for o in ops)
        dists = [strike_distance(o["option_type"], o.get("spot_favorable_45d"), o.get("strike_usd")) for o in ops]
        hit = sum(1 for d in dists if d is not None and d >= 0)
        avg = sum(to_float(o.get("retorno_neto_pct"), 0.0) for o in ops) / len(ops)
        rows.append((tp / len(ops), len(ops), t, tp, hit, avg))
    if not rows:
        return f"Aún no hay tickers con {min_ops} o más operaciones cerradas.\n" + DISCLAIMER
    rows.sort(key=lambda r: (-r[0], -r[1], r[2]))
    lines = [f"🏆 Tickers con más aciertos (mínimo {min_ops} operaciones cerradas)"]
    for i, (rate, n, t, tp, hit, avg) in enumerate(rows[:top], 1):
        lines.append(f"{i}. {t}: take-profit {tp}/{n} ({rate * 100:.0f}%) · llegó al strike {hit}/{n} · "
                     f"neto promedio {pct(avg)}")
    lines.append("Con pocas operaciones por ticker el % no es confiable. «Llegó al strike» = el subyacente "
                 "pasó el strike del protocolo en algún momento de los 45 días.")
    lines.append(DISCLAIMER)
    return _clip("\n".join(lines))


def respond(text: str, data: dict, cfg: dict, now: datetime, state: dict) -> str:
    """Texto de respuesta para un mensaje del usuario."""
    word = (text or "").strip().split()[0].lower() if (text or "").strip() else ""
    word = word.split("@")[0].lstrip("/")
    if word in ("estado", "resumen"):
        return cmd_estado(data, cfg, now, state)
    if word in ("abiertas", "abiertos"):
        return cmd_abiertas(data, cfg)
    if word in ("niveles", "nivel"):
        return cmd_niveles(data, cfg)
    if word in ("senales", "señales", "signals"):
        return cmd_senales(data, cfg, now)
    if word in ("aciertos", "tickers", "ranking"):
        return cmd_aciertos(data, cfg)
    return HELP
