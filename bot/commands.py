"""Respuestas del bot de Telegram a los comandos (funciones puras: datos -> texto).

Usan las mismas cuentas que los reportes (scripts/report_lib.py). El texto es plano (sin
Markdown) y cada respuesta cabe en un mensaje de Telegram. Dinero ficticio: analisis de datos,
no asesoria financiera.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from ops_engine import PROFIT_LEVELS
from report_lib import (MOTIVOS, VL_ESTADOS, distance_label, level_events, milestones, ops_of, pct,
                        strike_distance, summarize, usd, vl_easy, vl_ranking, vl_summary)
from common_utils import to_float

MAX_CHARS = 3900
DISCLAIMER = "Dinero ficticio; análisis de datos, no asesoría financiera."

HELP = (
    "Comandos disponibles:\n"
    "/estado – resumen: señales, avance hacia 100 y 300, retorno neto\n"
    "/abiertas – operaciones abiertas: retorno actual, nivel máximo y distancia al strike\n"
    "/niveles – últimos niveles +25/+50/+75/+100% alcanzados\n"
    "/senales – últimas señales de Vicente Luz y descartes de la semana\n"
    "/aciertos – tickers donde más acertó Vicente (su strike antes de su vencimiento)\n"
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


def vl_line(o: dict | None) -> str:
    """Estado de la señal frente al contrato de Vicente, en una linea."""
    if not o:
        return "Vicente: sin dato"
    e = o.get("vl_estado") or "sin_dato"
    k, v = o.get("strike_vl") or "sin dato", o.get("vl_vencimiento") or "sin dato"
    if e == "acerto":
        txt = f"acertó: tocó {k} el {o.get('vl_fecha_toque')} ({o.get('vl_dias_toque')} días)"
        txt += " — ya estaba en el dinero al publicarse" if vl_easy(o) else ""
    elif e == "no_acerto":
        txt = f"no acertó: venció el {v} sin tocar {k} (le faltó {pct(to_float(o.get('vl_mejor_pct')))})"
    elif e == "en_curso":
        best = to_float(o.get("vl_mejor_pct"))
        txt = f"en curso: strike {k}, vence {v} (" + ("aún sin precios posteriores a la señal" if best is None
                                                    else f"su mejor precio hasta hoy quedó a {pct(best)} del strike") + ")"
    else:
        txt = "sin dato (el mensaje no traía strike o vencimiento)"
    return "Vicente: " + txt


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
            f"{o.get('days_held') or '0'} días\n   {vl_line(o)}")
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
    by_sig = {o.get("signal_id"): o for o in ops_of(data, "VICENTE_LUZ")}
    lines = [f"📨 Últimas {len(valid)} señales válidas de Vicente Luz"]
    for s in valid:
        extra = " · ".join(x for x in (
            f"strike {s['strike_vl']}" if s.get("strike_vl") else "",
            f"vence {s['expiry_vl']}" if s.get("expiry_vl") else "",
            f"prima {s['price_recommended']}" if s.get("price_recommended") else "",
            f"spread {s['spread']}" if s.get("spread") else "") if x)
        lines.append(f"{_d(s['timestamp'])} UTC · {s['ticker']} {s['signal_type']}" + (f" · {extra}" if extra else "")
                     + f"\n   {vl_line(by_sig.get(s.get('signal_id')))}")
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


def cmd_aciertos(data: dict, cfg: dict, min_resolved: int = 2, top: int = 10) -> str:
    """Ranking por ticker: ¿el precio toco el strike de Vicente antes de SU vencimiento?"""
    ops = ops_of(data, "VICENTE_LUZ")
    rows = vl_ranking(ops, min_resolved)
    s = vl_summary(ops)
    if not rows:
        return f"Aún no hay tickers con {min_resolved} o más señales resueltas.\n" + DISCLAIMER
    lines = [f"🏆 Dónde más acertó Vicente (su strike antes de su vencimiento; mínimo {min_resolved} señales resueltas)",
             f"Total: acertó {s['aciertos']}/{s['resueltas']} ({pct(s['tasa'], 0, False)}); sin contar las que ya estaban "
             f"en el dinero al publicarse: {s['aciertos_dificiles']}/{s['dificiles']} ({pct(s['tasa_dificil'], 0, False)}). "
             f"En curso: {s['counts'].get('en_curso', 0)}."]
    for i, r in enumerate(rows[:top], 1):
        easy = f" ({r['faciles']} ya en el dinero)" if r["faciles"] else ""
        lines.append(f"{i}. {r['ticker']}: acertó {r['aciertos']}/{r['resueltas']} ({r['tasa']:.0f}%){easy} · "
                     f"en curso {r['en_curso']} · take-profit protocolo {r['tp']}/{r['cerradas']}")
    lines.append("Con pocas señales por ticker el % no es confiable. Fechas de cada señal: /senales.")
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
