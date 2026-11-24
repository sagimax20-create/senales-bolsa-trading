"""Script 3 — Velas de precio (yfinance) para los tickers con señales activas.

PROTOCOLO §7 pide seguimiento cada 15 minutos. GitHub Actions no garantiza correr cada 15 min
(en oct-nov corrio ~2 veces por dia), asi que en vez de una captura suelta por corrida, este
script descarga las VELAS de 15 minutos que falten desde la ultima guardada. Asi la entrada y
el +50% / -50% se evaluan con todas las velas, aunque GitHub se salte corridas.

  data/prices/<TICKER>.csv     velas de 15 min (Yahoo da 15 min solo de los ultimos 60 dias;
                               lo anterior se completa con velas de 60 min, columna interval)
  data/prices_1d/<TICKER>.csv  velas diarias del ultimo año (volatilidad realizada 20d)

Tickers: los de señales validas (Vicente Luz) y alertas (Gravity Zone) cuya operacion aun no
termino su seguimiento de 45 dias. Las de mas de 60 dias se completan con velas de 60 min.
Durante la sesion NYSE descarga en cada corrida; fuera de horario, como mucho cada
`price_offhours_min_interval_hours` horas (para cerrar el dia), salvo los tickers que aun no
tienen velas, que se descargan siempre. `--force` salta ese filtro.
Un ticker que falla se registra y no detiene a los demas. La vela en curso no se guarda.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common_utils import (BAR_COLS, DAILY_COLS, DAILY_DIR, GRAVITY_COLS, GRAVITY_CSV, NY,  # noqa: E402
                          OPERATIONS_CSV, OPS_COLS, PRICES_DIR, SIGNALS_COLS, SIGNALS_CSV, fmt_ts,
                          get_config, is_trading_day, load_csv, load_state, log_event, market_open,
                          now_utc, parse_ts, save_csv, ticker_file, to_float, update_state, yahoo_symbol)
from ops_engine import build_candidates  # noqa: E402
from signal_parser import PARSER_VERSION  # noqa: E402

STEP = {"15m": timedelta(minutes=15), "60m": timedelta(minutes=60)}
INTRADAY_15M_DAYS = 59   # Yahoo: velas de 15 min solo de los ultimos 60 dias


def needed_tickers(signals: list[dict], gravity: list[dict], cfg: dict, now: datetime,
                   ops: list[dict] | None = None) -> dict[str, datetime]:
    """ticker -> fecha de la señal mas antigua que aun necesita precios.

    Una señal deja de necesitar precios cuando su operacion esta cerrada y su seguimiento en
    sombra llego al dia 45 (congelada). Las que aun no tienen operacion (por ejemplo, todas las
    del reprocesamiento retroactivo) si necesitan, sin importar su antiguedad."""
    start = parse_ts(cfg["capture_start"] + " 00:00:00")
    done = {o["signal_key"] for o in (ops or [])
            if o.get("estado") == "closed" and str(o.get("sombra_completa")) == "1"}
    out: dict[str, datetime] = {}
    for c in build_candidates(signals, gravity, cfg):
        ts = parse_ts(c["ts"])
        if ts < start or ts > now or c["key"] in done:
            continue
        out[c["ticker"]] = min(out.get(c["ticker"], ts), ts)
    return out


def fetch_bars(ticker: str, start: datetime, end: datetime, interval: str) -> list[dict]:
    """Velas intradia de Yahoo (solo horario regular), en UTC y escaladas si es indice."""
    import yfinance as yf

    ysym, factor = yahoo_symbol(ticker)
    df = yf.Ticker(ysym).history(start=start, end=end, interval=interval, prepost=False,
                                 auto_adjust=False, actions=False)
    out = []
    for idx, row in df.iterrows():
        vals = [to_float(row.get(k)) for k in ("Open", "High", "Low", "Close")]
        if any(v is None or v <= 0 for v in vals):
            continue
        o, h, l, c = (v * factor for v in vals)
        out.append({"timestamp": fmt_ts(idx.to_pydatetime().astimezone(timezone.utc)),
                    "open": round(o, 4), "high": round(h, 4), "low": round(l, 4), "close": round(c, 4),
                    "volume": int(to_float(row.get("Volume"), 0) or 0), "interval": interval})
    return out


def fetch_daily(ticker: str) -> list[dict]:
    import yfinance as yf

    ysym, factor = yahoo_symbol(ticker)
    df = yf.Ticker(ysym).history(period="1y", interval="1d", auto_adjust=False, actions=False)
    out = []
    for idx, row in df.iterrows():
        vals = [to_float(row.get(k)) for k in ("Open", "High", "Low", "Close")]
        if any(v is None or v <= 0 for v in vals):
            continue
        out.append({"date": idx.date().isoformat(), **{k: round(v * factor, 4) for k, v in
                                                        zip(("open", "high", "low", "close"), vals)}})
    return out


def plan_ranges(have: list[dict], earliest: datetime, now: datetime) -> list[tuple[str, datetime, datetime]]:
    """Rangos (intervalo, desde, hasta) que faltan descargar para un ticker."""
    cut15 = now - timedelta(days=INTRADAY_15M_DAYS)
    lo = earliest - timedelta(days=1)
    ranges = []

    def add(a: datetime, b: datetime):
        if a >= b:
            return
        if a < cut15:
            ranges.append(("60m", a, min(b, cut15)))
            a = max(a, cut15)
        if a < b:
            ranges.append(("15m", a, b))

    if not have:
        add(lo, now)
        return ranges
    first, last = parse_ts(have[0]["timestamp"]), parse_ts(have[-1]["timestamp"])
    if earliest < first:   # una señal anterior a las velas guardadas: rellenar hacia atras
        add(lo, first)
    add(last + timedelta(seconds=1), now)
    return ranges


def merge_bars(have: list[dict], new: list[dict], now: datetime) -> list[dict]:
    """Une velas sin duplicar; si hay de 15 y 60 min en el mismo tramo, gana la de 15."""
    by_ts = {b["timestamp"]: b for b in have}
    for b in new:
        if parse_ts(b["timestamp"]) + STEP.get(b["interval"], STEP["15m"]) > now:
            continue  # vela en curso: aun no esta cerrada
        old = by_ts.get(b["timestamp"])
        if old is None or old.get("interval") != "15m":
            by_ts[b["timestamp"]] = b
    merged = sorted(by_ts.values(), key=lambda b: b["timestamp"])
    # las velas de 60 min solo cubren el tramo anterior a la primera vela de 15 min
    first15 = next((b["timestamp"] for b in merged if b.get("interval") == "15m"), None)
    return [b for b in merged if not (first15 and b.get("interval") == "60m" and b["timestamp"] >= first15)]


def update_ticker(ticker: str, earliest: datetime, now: datetime, fetcher=fetch_bars) -> int:
    path = PRICES_DIR / ticker_file(ticker)
    have = load_csv(path, BAR_COLS, create=False)
    new = []
    for interval, a, b in plan_ranges(have, earliest, now):
        new += fetcher(ticker, a, b, interval)
    if not new:
        return 0
    merged = merge_bars(have, new, now)
    if len(merged) != len(have):
        save_csv(path, BAR_COLS, merged, backup=False)
    return len(merged) - len(have)


def daily_is_stale(rows: list[dict], now: datetime) -> bool:
    if not rows:
        return True
    d = now.astimezone(NY).date() - timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return rows[-1]["date"] < d.isoformat()


def update_daily(ticker: str, now: datetime, fetcher=fetch_daily) -> bool:
    path = DAILY_DIR / ticker_file(ticker)
    rows = load_csv(path, DAILY_COLS, create=False)
    if not daily_is_stale(rows, now):
        return False
    new = fetcher(ticker)
    if new:
        by_date = {r["date"]: r for r in rows}
        by_date.update({r["date"]: r for r in new})
        save_csv(path, DAILY_COLS, [by_date[k] for k in sorted(by_date)], backup=False)
    return True


def select_tickers(tickers: dict[str, datetime], now: datetime, last_fetch: str | None, cfg: dict,
                   force: bool, has_bars) -> tuple[dict[str, datetime], str]:
    """Que tickers descargar en esta corrida: todos, o fuera de horario y con descarga reciente,
    solo los que aun no tienen velas (p. ej. tras reprocesar señales: sin esto esperarian horas)."""
    min_gap = timedelta(hours=float(cfg.get("price_offhours_min_interval_hours", 4)))
    paused = (not force and not market_open(now) and last_fetch is not None
              and now - parse_ts(last_fetch) < min_gap)
    if not paused:
        return tickers, "todos"
    new = {t: e for t, e in tickers.items() if not has_bars(t)}
    return new, ("solo_sin_velas" if new else "pausa")


def main() -> int:
    cfg = get_config()
    now = now_utc()
    state = load_state()
    if state.get("parser_version") != PARSER_VERSION:
        log_event("Script 3: esperando el reprocesamiento de señales; sin descarga")
        return 0
    signals = load_csv(SIGNALS_CSV, SIGNALS_COLS)
    gravity = load_csv(GRAVITY_CSV, GRAVITY_COLS)
    ops = load_csv(OPERATIONS_CSV, OPS_COLS) if state.get("ops_version") == 2 else []
    needed = needed_tickers(signals, gravity, cfg, now, ops)
    tickers, mode = select_tickers(needed, now, state.get("prices_last_fetch"), cfg, "--force" in sys.argv,
                                   lambda t: (PRICES_DIR / ticker_file(t)).exists())
    if mode == "pausa":
        log_event("Script 3: mercado cerrado y descarga reciente; sin descarga")
        return 0
    failed, n_new = [], 0
    for t, earliest in sorted(tickers.items()):
        try:
            update_daily(t, now)
            n_new += update_ticker(t, earliest, now)
        except Exception as e:
            failed.append(t)
            log_event(f"Script 3: {t} fallo: {type(e).__name__}: {e}", "WARNING")
    if mode == "todos":
        update_state(prices_last_fetch=fmt_ts(now))
    log_event(f"Script 3 ({mode}): {len(tickers)} tickers, {n_new} velas nuevas; fallidos: {failed or 'ninguno'}")
    return 1 if tickers and len(failed) == len(tickers) else 0


if __name__ == "__main__":
    sys.exit(main())
