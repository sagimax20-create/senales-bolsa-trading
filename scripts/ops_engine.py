"""Motor de operaciones en papel (puro: sin red ni archivos). Lo usa scripts/4_calculate_returns.py.

Cada señal valida abre UNA operacion de $1,000 ficticios (PROTOCOLO §2):
  entrada : apertura de la primera vela de 15 min que empieza en o despues de la señal
            (señal fuera de horario -> apertura de la siguiente sesion)
  strike  : CALL spot*1.05 / PUT spot*0.95        (OTM 95%)
  vence   : entrada + 45 dias
  prima   : Black-Scholes con IV = vol realizada 20d de cierres diarios previos a la entrada
            (MODELO: el pipeline no tiene cotizaciones reales de opciones)
Salidas (PROTOCOLO §3), la primera que ocurra, revisando cada vela:
  take_profit : la prima toca +50%  -> se registra exactamente +50% (orden limite)
  stop_loss   : la prima toca -50%  -> se registra -50%; si la vela ABRE por debajo (salto de
                precio), se registra el valor observado en la apertura, que puede ser peor
  expiry      : pasaron 45 dias     -> valor intrinseco con el ultimo cierre antes del vencimiento
  Si en una misma vela se tocan +50% y -50%, no se sabe cual fue primero: se asume el
  stop-loss (supuesto conservador, ver docs/BITACORA-IMPLEMENTACION.md).
Comision (PROTOCOLO §4): 1% del capital al entrar + 1% del valor al salir.
Sombra (PROTOCOLO §10 nota 2): la operacion se sigue hasta el dia 45 aunque haya cerrado, para
saber si habria tocado +100%, +150% o +200% y cuanto valia al vencimiento sin reglas. No
cambia el resultado oficial.
Niveles de ganancia (v3, analisis de la nota 2): primer momento en que la prima llega a +25%,
+50%, +75% y +100%, con el precio del subyacente en ese momento, para ver si se gano llegando
al strike o cerca de el. Tambien el precio mas favorable del subyacente en los 45 dias.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

from common_utils import NY, bs_price, calculate_strike, fmt_ts, is_trading_day, iv_for, parse_ts, to_float

OPS_VERSION = 4
PROFIT_LEVELS = (25, 50, 75, 100)   # % de ganancia de la prima
YEAR_SECONDS = 365 * 86400
_INTERVALS = {"15m": timedelta(minutes=15), "60m": timedelta(minutes=60), "1h": timedelta(hours=1)}


def commission_pct(gross_ret: float, cfg: dict) -> float:
    """Comision total como % del capital: 1% al entrar + 1% sobre el valor de salida."""
    c = cfg["protocol_commission"]
    return (c + c * (1 + gross_ret)) * 100


def _set_returns(op: dict, ret: float, cfg: dict) -> None:
    op["retorno_bruto_pct"] = round(ret * 100, 2)
    op["comision_pct"] = round(commission_pct(ret, cfg), 2)
    op["retorno_neto_pct"] = round(ret * 100 - commission_pct(ret, cfg), 2)


def _num_bars(bars: list[dict]) -> list[tuple[datetime, timedelta, float, float, float, float, str]]:
    out = []
    for b in bars:
        vals = [to_float(b.get(k)) for k in ("open", "high", "low", "close")]
        if any(v is None or v <= 0 for v in vals):
            continue
        iv = b.get("interval") or "15m"
        out.append((parse_ts(b["timestamp"]), _INTERVALS.get(iv, timedelta(minutes=15)), *vals, iv))
    out.sort(key=lambda x: x[0])
    return out


# --------------------------------------------------------------------------- acierto segun Vicente
def third_friday(year: int, month: int) -> date:
    """Vencimiento mensual estandar: tercer viernes (jueves si ese viernes es feriado NYSE)."""
    d = date(year, month, 15)
    while d.weekday() != 4:
        d += timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def vl_expiry_date(expiry_vl: str) -> date | None:
    """Fecha de vencimiento publicada por Vicente: AAAA-MM-DD, o AAAA-MM -> tercer viernes."""
    try:
        if len(expiry_vl or "") == 10:
            return date.fromisoformat(expiry_vl)
        if len(expiry_vl or "") == 7:
            y, m = (int(x) for x in expiry_vl.split("-"))
            return third_friday(y, m)
    except ValueError:
        return None
    return None


def _spread_target(spread: str, view: str) -> float | None:
    """Strike que el precio debe superar al vencimiento para la ganancia maxima del spread."""
    m = re.match(r"(CALL|PUT)\s+([\d.]+)/([\d.]+)", spread or "")
    if not m:
        return None
    a, b = float(m.group(2)), float(m.group(3))
    return max(a, b) if view == "CALL" else min(a, b)


def vicente_outcome(info: dict, bars: list[dict], daily: list[dict], now: datetime) -> dict:
    """¿El precio llego al strike de Vicente entre la señal y SU fecha de vencimiento?

    info: option_type (CALL/PUT = direccion), signal_timestamp, strike_vl, expiry_vl, spread_vl.
    Opcion simple: acierta si el maximo (CALL) o el minimo (PUT) toca el strike antes del
    vencimiento; el dia de la señal solo cuentan las velas de 15 min posteriores a la señal.
    Spread: acierta si al cierre del dia de vencimiento el precio quedo en la zona de ganancia
    maxima. Estados: acerto | no_acerto | en_curso | sin_dato."""
    out = {"vl_vencimiento": "", "vl_estado": "sin_dato", "vl_fecha_toque": "", "vl_dias_toque": "",
           "vl_mejor_pct": "", "vl_dist_inicial_pct": ""}
    strike, exp = to_float(info.get("strike_vl")), vl_expiry_date(info.get("expiry_vl") or "")
    view = info["option_type"]
    sig_ts = parse_ts(info["signal_timestamp"])
    sig_day = sig_ts.astimezone(NY).date()
    if exp:
        out["vl_vencimiento"] = exp.isoformat()
    target = _spread_target(info.get("spread_vl") or "", view)
    level = target if target is not None else strike
    if level is None or exp is None or exp < sig_day:
        return out
    today = now.astimezone(NY).date()
    last_td = exp
    while not is_trading_day(last_td) and last_td > sig_day:
        last_td -= timedelta(days=1)
    better = (lambda x, y: x >= y) if view == "CALL" else (lambda x, y: x <= y)
    # camino de precios favorables: velas de 15 min del dia de la señal + velas diarias posteriores
    path: list[tuple[date, float]] = []
    ref = None   # precio al publicarse la señal: apertura de la primera vela posterior
    for b_start, b_step, o, h, l, _c, _iv in _num_bars(bars):
        if b_start >= sig_ts and ref is None:
            ref = o
        if b_start >= sig_ts and b_start.astimezone(NY).date() == sig_day <= exp:
            path.append((sig_day, h if view == "CALL" else l))
    closes = {}
    for r in sorted(daily, key=lambda r: r["date"]):
        d = date.fromisoformat(r["date"])
        hi, lo, cl = to_float(r.get("high")), to_float(r.get("low")), to_float(r.get("close"))
        if hi is None or lo is None:
            continue
        closes[d] = cl
        if ref is None and d > sig_day:
            ref = to_float(r.get("open")) or cl
        if sig_day < d <= exp:
            path.append((d, hi if view == "CALL" else lo))
    if ref:   # >= 0: el precio ya estaba en el strike o mas alla al publicarse (acierto "facil")
        out["vl_dist_inicial_pct"] = round(((ref - level) / level if view == "CALL" else (level - ref) / level) * 100, 2)
    if path:
        best = max(p for _, p in path) if view == "CALL" else min(p for _, p in path)
        out["vl_mejor_pct"] = round(((best - level) / level if view == "CALL" else (level - best) / level) * 100, 2)
    covered = last_td in closes and today > exp or (today == exp and last_td in closes and now.astimezone(NY).time() >= time(16, 30))
    if target is not None:   # spread: se decide al cierre del vencimiento
        if covered:
            ok = closes.get(last_td) is not None and better(closes[last_td], target)
            out["vl_estado"] = "acerto" if ok else "no_acerto"
            if ok:
                out["vl_fecha_toque"], out["vl_dias_toque"] = last_td.isoformat(), (last_td - sig_day).days
        else:
            out["vl_estado"] = "en_curso"
        return out
    touch = next((d for d, p in path if better(p, strike)), None)
    if touch:
        out.update({"vl_estado": "acerto", "vl_fecha_toque": touch.isoformat(), "vl_dias_toque": (touch - sig_day).days})
    elif covered:
        out["vl_estado"] = "no_acerto"
    elif not closes and today > exp + timedelta(days=5):
        out["vl_estado"] = "sin_dato"   # sin precios del ticker
    else:
        out["vl_estado"] = "en_curso"
    return out


def evaluate(cand: dict, bars: list[dict], daily: list[dict], cfg: dict, now: datetime) -> dict:
    """Operacion completa para una señal, recalculada desde las velas (determinista)."""
    op = {"ticker": cand["ticker"], "option_type": cand["type"], "channel": cand["channel"],
          "signal_timestamp": cand["ts"], "signal_key": cand["key"], "signal_id": cand.get("signal_id", ""),
          "message_id": cand.get("message_id", ""), "en_universo_original": cand.get("original", ""),
          "strike_vl": cand.get("strike_vl", ""), "expiry_vl": cand.get("expiry_vl", ""),
          "spread_vl": cand.get("spread", ""), "ops_version": OPS_VERSION, "sombra_completa": 0}
    if op["channel"] == "VICENTE_LUZ":
        op.update(vicente_outcome(op, bars, daily, now))
    sig_ts = parse_ts(cand["ts"])
    nb = _num_bars(bars)
    entry_i = next((i for i, b in enumerate(nb) if b[0] >= sig_ts), None)
    if entry_i is None:
        op["estado"] = "sin_precio" if now - sig_ts > timedelta(days=5) else "pending"
        return op

    entry_ts, step, spot, *_rest, interval = nb[entry_i]
    entry_date_ny = entry_ts.astimezone(NY).date()
    closes = [to_float(d["close"]) for d in sorted(daily, key=lambda d: d["date"])
              if d["date"] < entry_date_ny.isoformat() and to_float(d.get("close"))]
    iv = iv_for(op["ticker"], closes, cfg)
    otype = op["option_type"]
    strike = calculate_strike(spot, cfg["protocol_strike_otm"], otype)
    days = int(cfg["protocol_days_expiry"])
    expiry = entry_ts + timedelta(days=days)
    r = cfg["risk_free_rate"]
    p0 = max(bs_price(spot, strike, days / 365, iv, r, otype), 0.01)
    tp, sl = cfg["protocol_take_profit"], cfg["protocol_stop_loss"]
    op.update({"timestamp_entry": fmt_ts(entry_ts), "spot_entry": round(spot, 4), "iv_entry": round(iv, 4),
               "strike_usd": strike, "expiry": fmt_ts(expiry), "price_entry": round(p0, 4),
               "bar_interval": interval, "estado": "open"})

    def ret_at(s: float, t: datetime) -> float:
        ty = max((expiry - t).total_seconds(), 0) / YEAR_SECONDS
        return bs_price(s, strike, ty, iv, r, otype) / p0 - 1

    closed = None   # (ts, prima_salida, retorno, regla, spot)
    peak = trough = peak45 = trough45 = 0.0
    levels: dict[int, tuple[datetime, float]] = {}   # nivel -> (momento, precio del subyacente)
    fav_spot = spot
    last_end, last_close, last_ret = entry_ts, spot, 0.0
    expired = False
    for b_start, b_step, o, h, l, c, _iv in nb[entry_i:]:
        if b_start >= expiry:
            expired = True
            break
        b_end = b_start + b_step
        fav, unf = (h, l) if otype == "CALL" else (l, h)
        r_open, r_hi, r_lo, r_close = ret_at(o, b_start), ret_at(fav, b_end), ret_at(unf, b_end), ret_at(c, b_end)
        peak45, trough45 = max(peak45, r_open, r_hi), min(trough45, r_open, r_lo)
        fav_spot = max(fav_spot, fav) if otype == "CALL" else min(fav_spot, fav)
        for lv in PROFIT_LEVELS:
            if lv not in levels:
                if r_open >= lv / 100:
                    levels[lv] = (b_start, o)       # la vela abrio ya por encima del nivel
                elif r_hi >= lv / 100:
                    levels[lv] = (b_end, fav)
        if closed is None:
            peak, trough = max(peak, r_open, r_hi), min(trough, r_open, r_lo)
            if r_open <= sl:
                closed = (b_start, p0 * (1 + r_open), r_open, "stop_loss", o)
            elif r_open >= tp:
                closed = (b_start, p0 * (1 + tp), tp, "take_profit", o)
            elif r_lo <= sl:   # incluye el caso de tocar ambos en la misma vela (conservador)
                closed = (b_end, p0 * (1 + sl), sl, "stop_loss", unf)
            elif r_hi >= tp:
                closed = (b_end, p0 * (1 + tp), tp, "take_profit", fav)
        last_end, last_close, last_ret = b_end, c, r_close
    if not expired and now >= expiry + timedelta(days=3):
        expired = True   # sin velas nuevas (fin de semana, ticker sin datos): se cierra igual

    op["spot_favorable_45d"] = round(fav_spot, 4)
    for lv in PROFIT_LEVELS:
        ts_lv, spot_lv = levels.get(lv, (None, None))
        op[f"nivel_{lv}_ts"] = fmt_ts(ts_lv) if ts_lv else ""
        op[f"nivel_{lv}_spot"] = round(spot_lv, 4) if spot_lv else ""
    op.update({"peak_45d_pct": round(peak45 * 100, 2), "trough_45d_pct": round(trough45 * 100, 2),
               "toco_100": int(peak45 >= 1.0), "toco_150": int(peak45 >= 1.5), "toco_200": int(peak45 >= 2.0)})
    if expired:
        intrinsic = bs_price(last_close, strike, 0, iv, r, otype)
        r_exp = intrinsic / p0 - 1
        op["ret_sin_reglas_pct"] = round(r_exp * 100, 2)
        op["sombra_completa"] = 1
        if closed is None:
            closed = (expiry, intrinsic, r_exp, "expiry", last_close)

    if closed:
        ts, prem, ret, reason, s_exit = closed
        op.update({"estado": "closed", "price_exit": round(prem, 4), "reason_exit": reason,
                   "timestamp_exit": fmt_ts(ts), "spot_exit": round(s_exit, 4),
                   "days_held": round((ts - entry_ts).total_seconds() / 86400, 2),
                   "peak_return_pct": round(peak * 100, 2), "trough_return_pct": round(trough * 100, 2)})
        _set_returns(op, ret, cfg)
    else:  # sigue abierta: retorno provisional marcado a mercado (modelado)
        op.update({"days_held": round((last_end - entry_ts).total_seconds() / 86400, 2),
                   "peak_return_pct": round(peak * 100, 2), "trough_return_pct": round(trough * 100, 2),
                   "spot_exit": "", "price_exit": "", "reason_exit": "", "timestamp_exit": ""})
        _set_returns(op, last_ret, cfg)
    return op


def build_candidates(signals: list[dict], gravity: list[dict], cfg: dict) -> list[dict]:
    """Señales que abren operacion: Vicente Luz validas (universo ampliado, ADENDA-01) y, si el
    canal esta activo, alertas de Gravity Zone (8 tickers). Gravity Zone se suspendio el
    2026-11-24 (ADENDA-02): gravity_enabled = false en config.json."""
    start = cfg["capture_start"] + " 00:00:00"
    allowed = set(cfg["allowed_tickers"])
    out = []
    for r in signals:
        if r.get("estado") != "valida" or r["signal_type"] not in ("CALL", "PUT") or r["timestamp"] < start:
            continue
        out.append({"ts": r["timestamp"], "ticker": r["ticker"], "type": r["signal_type"], "channel": r["channel"],
                    "key": f"{r['channel']}|{r['signal_id']}", "signal_id": r["signal_id"],
                    "message_id": r["message_id"], "original": "1" if r["ticker"] in allowed else "0",
                    "strike_vl": r.get("strike_vl", ""), "expiry_vl": r.get("expiry_vl", ""),
                    "spread": r.get("spread", "")})
    for r in (gravity if cfg.get("gravity_enabled", True) else []):
        if r["ticker"] not in allowed or r["signal_type"] not in ("CALL", "PUT") or r["timestamp"] < start:
            continue
        out.append({"ts": r["timestamp"], "ticker": r["ticker"], "type": r["signal_type"], "channel": r["channel"],
                    "key": f"{r['channel']}|{r['timestamp']}|{r['ticker']}|{r['signal_type']}",
                    "signal_id": "", "message_id": "", "original": "1"})
    return sorted(out, key=lambda c: (c["ts"], c["key"]))
