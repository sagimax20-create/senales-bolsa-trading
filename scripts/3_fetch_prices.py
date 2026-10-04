"""Script 3 — Captura de precios de los 8 tickers (yfinance) cada 15 min en horario NYSE.

Agrega una fila por ticker a data/daily_prices.csv:
  timestamp (UTC), ticker, price_spot, price_high, price_low, volatility_pct, iv_estimate
- price_high / price_low: maximo / minimo del dia en curso.
- volatility_pct: (high-low)/low*100, rango del dia.
- iv_estimate: volatilidad realizada 20d anualizada (proxy; NO es IV de mercado).

Fuera del horario NYSE, o en feriado (la ultima vela no es de hoy), no escribe nada.
Un ticker que falla se registra y no detiene a los demas. `--force` salta el filtro horario.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common_utils import (NY, PRICES_COLS, PRICES_CSV, append_csv, fmt_ts, get_config,  # noqa: E402
                          get_daily_history, iv_for, log_event, market_open, now_utc)


def main() -> int:
    cfg = get_config()
    now = now_utc()
    if "--force" not in sys.argv and not market_open(now):
        log_event("Script 3: mercado cerrado; sin captura")
        return 0
    today_ny = now.astimezone(NY).date()
    rows, failed = [], []
    for t in cfg["allowed_tickers"]:
        try:
            hist = get_daily_history(t, "3mo")
            last = hist[-1]
            if last["date"] != today_ny and "--force" not in sys.argv:
                log_event(f"Script 3: {t} sin vela de hoy ({last['date']}); posible feriado", "WARNING")
                continue
            rows.append({
                "timestamp": fmt_ts(now), "ticker": t,
                "price_spot": round(last["close"], 4), "price_high": round(last["high"], 4),
                "price_low": round(last["low"], 4),
                "volatility_pct": round((last["high"] - last["low"]) / last["low"] * 100, 3),
                "iv_estimate": round(iv_for(t, [h["close"] for h in hist], cfg), 4),
            })
        except Exception as e:
            failed.append(t)
            log_event(f"Script 3: {t} fallo: {type(e).__name__}: {e}", "ERROR")
    n = append_csv(PRICES_CSV, PRICES_COLS, rows)
    log_event(f"Script 3: {n} precios guardados; fallidos: {failed or 'ninguno'}")
    return 1 if failed and not rows else 0


if __name__ == "__main__":
    sys.exit(main())
