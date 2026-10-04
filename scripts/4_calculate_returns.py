"""Script 4 — Convierte señales en operaciones y aplica las 3 reglas de salida del PROTOCOLO.

Cada señal (Vicente Luz o Gravity Zone) abre UNA operacion de $1,000 ficticios:
  entrada : primer precio capturado en/despues de la señal (señal fuera de horario -> entra
            en la siguiente captura, normalmente la apertura)
  strike  : CALL spot*1.05 / PUT spot*0.95        (OTM 95%)
  vence   : entrada + 45 dias
  prima   : Black-Scholes con IV = vol realizada 20d (MODELO, no cotizacion real)
Salidas, la primera que ocurra (se reproduce toda la serie de precios, asi que un cruce
no se pierde aunque el script corra tarde):
  take_profit : retorno de la prima >= +50%  -> se registra exactamente +50%
  stop_loss   : retorno <= -50%              -> se registra el retorno modelado observado
                (si hubo gap por la noche, la perdida real se ve, no se maquilla a -50%)
  expiry      : pasaron 45 dias              -> valor intrinseco al cierre
Comision: 1% del capital al entrar + 1% del valor al salir. peak/trough registran lo que
habria pasado con otros umbrales (PROTOCOLO, nota 2) sin alterar la regla.
Estados: pending (aun sin precio de entrada), open, closed.
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common_utils import (GRAVITY_COLS, GRAVITY_CSV, OPERATIONS_CSV, OPS_COLS, PRICES_COLS,  # noqa: E402
                          PRICES_CSV, SIGNALS_COLS, SIGNALS_CSV, bs_price, calculate_strike, fmt_ts,
                          get_config, load_csv, log_event, parse_ts, save_csv, to_float)


def build_candidates(signals: list[dict], gravity: list[dict], cfg: dict) -> list[dict]:
    start = cfg["capture_start"] + " 00:00:00"
    allowed = set(cfg["allowed_tickers"])
    out = []
    for r in signals + gravity:
        if r["ticker"] not in allowed or r["signal_type"] not in ("CALL", "PUT") or r["timestamp"] < start:
            continue
        out.append({"ts": r["timestamp"], "ticker": r["ticker"], "type": r["signal_type"],
                    "channel": r["channel"],
                    "key": f"{r['channel']}|{r['timestamp']}|{r['ticker']}|{r['signal_type']}"})
    return sorted(out, key=lambda c: c["ts"])


def commission_pct(gross_ret: float, cfg: dict) -> float:
    """Comision total como % del capital: 1% al entrar + 1% sobre el valor de salida."""
    c = cfg["protocol_commission"]
    return (c + c * (1 + gross_ret)) * 100


def _close(op: dict, ts, exit_prem: float, ret: float, reason: str, entry_ts, cfg: dict) -> None:
    op.update({
        "price_exit": round(exit_prem, 4), "reason_exit": reason, "estado": "closed",
        "timestamp_exit": fmt_ts(ts), "days_held": round((ts - entry_ts).total_seconds() / 86400, 2),
    })
    _set_returns(op, ret, cfg)


def _set_returns(op: dict, ret: float, cfg: dict) -> None:
    op["retorno_bruto_pct"] = round(ret * 100, 2)
    op["comision_pct"] = round(commission_pct(ret, cfg), 2)
    op["retorno_neto_pct"] = round(ret * 100 - commission_pct(ret, cfg), 2)


def try_enter(op: dict, rows: list[dict], cfg: dict) -> bool:
    """Intenta fijar la entrada de una operacion pending con el primer precio >= señal."""
    for p in rows:
        if p["timestamp"] >= op["signal_timestamp"]:
            spot = to_float(p["price_spot"])
            if spot is None or spot <= 0:
                continue
            iv = to_float(p["iv_estimate"]) or float(cfg["iv_fallback"].get(op["ticker"], 0.30))
            entry_ts = parse_ts(p["timestamp"])
            strike = calculate_strike(spot, cfg["protocol_strike_otm"], op["option_type"])
            op.update({
                "timestamp_entry": p["timestamp"], "spot_entry": round(spot, 4), "iv_entry": round(iv, 4),
                "strike_usd": strike, "estado": "open", "peak_return_pct": 0, "trough_return_pct": 0,
                "expiry": fmt_ts(entry_ts + timedelta(days=int(cfg["protocol_days_expiry"]))),
            })
            op["price_entry"] = round(_premium(op, spot, entry_ts, cfg), 4)
            return True
    return False


def _premium(op: dict, spot: float, ts, cfg: dict) -> float:
    t_years = max((parse_ts(op["expiry"]) - ts).total_seconds(), 0) / (365 * 86400)
    return bs_price(spot, float(op["strike_usd"]), t_years, float(op["iv_entry"]),
                    cfg["risk_free_rate"], op["option_type"])


def replay(op: dict, rows: list[dict], cfg: dict) -> None:
    """Reproduce los precios posteriores a la entrada y cierra la operacion si toca una regla."""
    entry_ts = parse_ts(op["timestamp_entry"])
    expiry = parse_ts(op["expiry"])
    # La prima de entrada SIEMPRE se recalcula con los campos guardados (determinista).
    p0 = max(bs_price(float(op["spot_entry"]), float(op["strike_usd"]),
                      int(cfg["protocol_days_expiry"]) / 365, float(op["iv_entry"]),
                      cfg["risk_free_rate"], op["option_type"]), 0.01)
    tp, sl = cfg["protocol_take_profit"], cfg["protocol_stop_loss"]
    peak = trough = 0.0
    last_ts, last_ret = entry_ts, 0.0
    for p in rows:
        if p["timestamp"] <= op["timestamp_entry"]:
            continue
        spot = to_float(p["price_spot"])
        if spot is None or spot <= 0:
            continue
        ts = parse_ts(p["timestamp"])
        prem = _premium(op, spot, ts, cfg)
        ret = prem / p0 - 1
        peak, trough = max(peak, ret), min(trough, ret)
        op["peak_return_pct"], op["trough_return_pct"] = round(peak * 100, 2), round(trough * 100, 2)
        last_ts, last_ret = ts, ret
        if ret >= tp:
            _close(op, ts, p0 * (1 + tp), tp, "take_profit", entry_ts, cfg)
            return
        if ret <= sl:
            _close(op, ts, prem, ret, "stop_loss", entry_ts, cfg)
            return
        if ts >= expiry:
            _close(op, ts, prem, ret, "expiry", entry_ts, cfg)
            return
    # sigue abierta: retorno provisional marcado a mercado (modelado)
    op["days_held"] = round((last_ts - entry_ts).total_seconds() / 86400, 2)
    _set_returns(op, last_ret, cfg)


def main() -> int:
    cfg = get_config()
    signals = load_csv(SIGNALS_CSV, SIGNALS_COLS)
    gravity = load_csv(GRAVITY_CSV, GRAVITY_COLS)
    prices = load_csv(PRICES_CSV, PRICES_COLS)
    ops = load_csv(OPERATIONS_CSV, OPS_COLS)

    by_ticker = defaultdict(list)
    for p in prices:
        by_ticker[p["ticker"]].append(p)
    for lst in by_ticker.values():
        lst.sort(key=lambda p: p["timestamp"])

    known = {o["signal_key"] for o in ops}
    next_id = max([int(o["id"]) for o in ops if o["id"]] or [0]) + 1
    created = 0
    for c in build_candidates(signals, gravity, cfg):
        if c["key"] in known:
            continue
        known.add(c["key"])
        ops.append({"id": next_id, "ticker": c["ticker"], "option_type": c["type"], "estado": "pending",
                    "channel": c["channel"], "signal_timestamp": c["ts"], "signal_key": c["key"]})
        next_id += 1
        created += 1

    entered = closed = 0
    for op in ops:
        try:
            if op["estado"] == "closed":
                continue
            rows = by_ticker.get(op["ticker"], [])
            if op["estado"] == "pending":
                if not try_enter(op, rows, cfg):
                    continue
                entered += 1
            replay(op, rows, cfg)
            closed += op["estado"] == "closed"
        except Exception as e:  # una operacion rara no debe frenar a las demas
            log_event(f"Script 4: op {op.get('id')} ({op.get('ticker')}) fallo: {type(e).__name__}: {e}", "ERROR")

    save_csv(OPERATIONS_CSV, OPS_COLS, ops)
    states = defaultdict(int)
    for o in ops:
        states[o["estado"]] += 1
    log_event(f"Script 4: +{created} operaciones, {entered} entradas, {closed} cierres nuevos; "
              f"total {len(ops)} {dict(states)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
