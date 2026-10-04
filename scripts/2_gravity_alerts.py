"""Script 2 — Alertas "Gravity Zone" (proxy).

OJO: no existe API publica de Gravity Zone en este proyecto. Esta es una APROXIMACION:
  gz_level = (precio actual - media 20d) / desviacion estandar 20d      (z-score diario)
y los umbrales de TICKERS-PARA-SEGUIMIENTO-GRAVITY.md:
  gz_level <= -2.0 y rango del dia <= 1.5%  -> CALL (sobreventa con compresion)
  gz_level >= +2.0 y rango del dia >= 3.5%  -> PUT  (sobrecompra con expansion)
  en cualquier otro caso                    -> sin señal
  (rango del dia = (high-low)/low*100; umbrales en config.json: gravity_vol_*)
Si Rildo consigue el indicador real (p. ej. alertas de TradingView), se reemplaza solo la
funcion gz_level() sin tocar el resto del pipeline.

Anti-duplicados: no repite la misma alerta (ticker + direccion) dentro de
`gravity_dedupe_hours` (config.json). Para swing 1D el valor por defecto es 24 h; el prompt
decia 1 h, pero con velas diarias eso generaria ~7 señales casi identicas por dia y
falsearia el conteo de 300 señales.

Solo corre en horario NYSE; `--force` salta el filtro. Escribe data/gravity_alerts.csv.
"""
from __future__ import annotations

import os
import statistics
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common_utils import (GRAVITY_COLS, GRAVITY_CSV, append_csv, fmt_ts, get_config,  # noqa: E402
                          get_daily_history, load_csv, log_event, market_open, now_utc, parse_ts)

CHANNEL_TAG = "GRAVITY_ZONE"


def gz_level(closes: list[float], lookback: int) -> float | None:
    """z-score del ultimo cierre contra los ultimos `lookback` cierres (incluye el actual)."""
    window = closes[-lookback:]
    if len(window) < max(10, lookback // 2):
        return None
    sd = statistics.stdev(window)
    return None if sd == 0 else (window[-1] - statistics.mean(window)) / sd


def main() -> int:
    cfg = get_config()
    now = now_utc()
    if "--force" not in sys.argv and not market_open(now):
        log_event("Script 2: mercado cerrado; sin alertas")
        return 0
    existing = load_csv(GRAVITY_CSV, GRAVITY_COLS)
    cutoff = now - timedelta(hours=float(cfg["gravity_dedupe_hours"]))
    recent = {(r["ticker"], r["signal_type"]) for r in existing if parse_ts(r["timestamp"]) >= cutoff}

    rows, failed = [], []
    for t in cfg["allowed_tickers"]:
        try:
            hist = get_daily_history(t, "3mo")
            level = gz_level([h["close"] for h in hist], int(cfg["gravity_lookback_days"]))
            if level is None:
                continue
            # Capturar volatilidad actual del dia
            try:
                vol_pct = (hist[-1]["high"] - hist[-1]["low"]) / hist[-1]["low"] * 100
            except (KeyError, ZeroDivisionError, TypeError):
                vol_pct = 0.0
            if level <= cfg["gravity_call_level"]:
                # CALL solo si hay compresion (vol < 1.5%)
                if vol_pct > cfg.get("gravity_vol_compression_call", 1.5):
                    continue
                direction = "CALL"
            elif level >= cfg["gravity_put_level"]:
                # PUT solo si hay expansion (vol > 3.5%)
                if vol_pct < cfg.get("gravity_vol_expansion_put", 3.5):
                    continue
                direction = "PUT"
            else:
                continue
            if (t, direction) in recent:
                continue
            rows.append({"timestamp": fmt_ts(now), "ticker": t, "signal_type": direction,
                         "gz_level": round(level, 2), "price_spot": round(hist[-1]["close"], 4),
                         "channel": CHANNEL_TAG})
        except Exception as e:
            failed.append(t)
            log_event(f"Script 2: {t} fallo: {type(e).__name__}: {e}", "ERROR")
    n = append_csv(GRAVITY_CSV, GRAVITY_COLS, rows)
    log_event(f"Script 2: {n} alertas nuevas; fallidos: {failed or 'ninguno'}")
    return 1 if failed and not rows else 0


if __name__ == "__main__":
    sys.exit(main())
