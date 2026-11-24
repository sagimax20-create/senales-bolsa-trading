"""Script 2 — Alertas "Gravity Zone" (aproximacion), evaluadas al CIERRE de cada dia habil.

OJO: el indicador Gravity Zone real (TradingView) no esta conectado a este proyecto. Esta es una
APROXIMACION creada por la implementacion original; sus umbrales NO estan en PROTOCOLO.md:
  gz_level = (cierre - media 20d) / desviacion estandar 20d            (z-score diario)
  gz_level <= -2.0 y rango del dia <= 1.5%  -> CALL (sobreventa con compresion)
  gz_level >= +2.0 y rango del dia >= 3.5%  -> PUT  (sobrecompra con expansion)
  en cualquier otro caso                    -> sin señal
  (rango del dia = (high-low)/low*100; umbrales en config.json: gravity_*)
Si se conecta el indicador real (p. ej. alertas de TradingView), se reemplaza este script.

v2 (2026-11-24): la regla es la misma, pero se evalua el cierre de CADA dia habil desde
capture_start, con las velas diarias guardadas. Antes se evaluaba solo cuando GitHub corria
(una vez por dia en oct-nov, con la vela del dia a medio formar) y el filtro de 24 h dejo
fuera la alerta de GLD del 23-oct. Ahora hay como mucho una alerta por ticker, direccion y
dia; la hora de la alerta es el cierre (16:00 Nueva York) y la entrada en papel es la apertura
siguiente. La primera corrida respalda gravity_alerts.csv (v1) y lo reconstruye.

Tickers: los 8 del protocolo (allowed_tickers). Escribe data/gravity_alerts.csv.
"""
from __future__ import annotations

import os
import statistics
import sys
from datetime import date, datetime, time, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common_utils import (DAILY_COLS, DAILY_DIR, GRAVITY_COLS, GRAVITY_CSV, NY, backup_once,  # noqa: E402
                          fmt_ts, get_config, get_daily_history, is_trading_day, load_csv, load_state,
                          log_event, now_utc, save_csv, ticker_file, to_float, update_state)

CHANNEL_TAG = "GRAVITY_ZONE"
GRAVITY_VERSION = 2


def gz_level(closes: list[float], lookback: int) -> float | None:
    """z-score del ultimo cierre contra los ultimos `lookback` cierres (incluye el actual)."""
    window = closes[-lookback:]
    if len(window) < max(10, lookback // 2):
        return None
    sd = statistics.stdev(window)
    return None if sd == 0 else (window[-1] - statistics.mean(window)) / sd


def last_closed_day(now: datetime) -> date:
    """Ultimo dia habil NYSE cuyo cierre ya ocurrio (con 30 min de margen)."""
    ny = now.astimezone(NY)
    d = ny.date()
    if not (is_trading_day(d) and ny.time() >= time(16, 30)):
        d -= timedelta(days=1)
        while not is_trading_day(d):
            d -= timedelta(days=1)
    return d


def close_timestamp(d: date) -> str:
    return fmt_ts(datetime(d.year, d.month, d.day, 16, 0, tzinfo=NY))


def daily_alerts(ticker: str, rows: list[dict], cfg: dict, start: date, until: date) -> list[dict]:
    """Alertas de un ticker evaluando el cierre de cada dia habil en [start, until]."""
    rows = sorted((r for r in rows if to_float(r.get("close"))), key=lambda r: r["date"])
    lookback = int(cfg["gravity_lookback_days"])
    out = []
    for i, r in enumerate(rows):
        d = date.fromisoformat(r["date"])
        if d < start or d > until or not is_trading_day(d):
            continue
        level = gz_level([float(x["close"]) for x in rows[:i + 1]], lookback)
        high, low = to_float(r.get("high")), to_float(r.get("low"))
        if level is None or not high or not low:
            continue
        vol_pct = (high - low) / low * 100
        if level <= cfg["gravity_call_level"] and vol_pct <= cfg.get("gravity_vol_compression_call", 1.5):
            direction = "CALL"
        elif level >= cfg["gravity_put_level"] and vol_pct >= cfg.get("gravity_vol_expansion_put", 3.5):
            direction = "PUT"
        else:
            continue
        out.append({"timestamp": close_timestamp(d), "ticker": ticker, "signal_type": direction,
                    "gz_level": round(level, 2), "price_spot": round(float(r["close"]), 4),
                    "channel": CHANNEL_TAG})
    return out


def refresh_daily(ticker: str, until: date, fetcher=get_daily_history) -> list[dict]:
    """Velas diarias guardadas del ticker; descarga si faltan dias hasta `until`."""
    path = DAILY_DIR / ticker_file(ticker)
    rows = load_csv(path, DAILY_COLS, create=False)
    if rows and rows[-1]["date"] >= until.isoformat():
        return rows
    new = [{"date": h["date"].isoformat(), **{k: round(h[k], 4) for k in ("open", "high", "low", "close")}}
           for h in fetcher(ticker, "1y")]
    by_date = {r["date"]: r for r in rows}
    by_date.update({r["date"]: r for r in new})
    rows = [by_date[k] for k in sorted(by_date)]
    save_csv(path, DAILY_COLS, rows, backup=False)
    return rows


def main() -> int:
    cfg = get_config()
    now = now_utc()
    state = load_state()
    start = date.fromisoformat(cfg["capture_start"])
    until = last_closed_day(now)
    if state.get("gravity_version") == GRAVITY_VERSION and state.get("gravity_evaluated_until") == until.isoformat():
        log_event(f"Script 2: cierre del {until} ya evaluado; nada que hacer")
        return 0
    alerts, failed = [], []
    for t in cfg["allowed_tickers"]:
        try:
            alerts += daily_alerts(t, refresh_daily(t, until), cfg, start, until)
        except Exception as e:
            failed.append(t)
            log_event(f"Script 2: {t} fallo: {type(e).__name__}: {e}", "WARNING")
    if failed:  # sin datos completos no se reescribe: se reintenta en la proxima corrida
        log_event(f"Script 2: sin datos de {failed}; alertas sin cambios", "WARNING")
        return 1
    if state.get("gravity_version") != GRAVITY_VERSION:
        backup_once(GRAVITY_CSV, f"gravity_alerts_v1_{now:%Y%m%d}.csv")
    before = {(r["timestamp"], r["ticker"], r["signal_type"]) for r in load_csv(GRAVITY_CSV, GRAVITY_COLS)}
    alerts.sort(key=lambda r: (r["timestamp"], r["ticker"]))
    save_csv(GRAVITY_CSV, GRAVITY_COLS, alerts)
    new = [a for a in alerts if (a["timestamp"], a["ticker"], a["signal_type"]) not in before]
    update_state(gravity_version=GRAVITY_VERSION, gravity_evaluated_until=until.isoformat())
    log_event(f"Script 2: cierres evaluados hasta {until}; {len(alerts)} alertas en total, {len(new)} nuevas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
