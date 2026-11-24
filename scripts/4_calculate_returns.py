"""Script 4 — Convierte señales validas en operaciones en papel (reglas en scripts/ops_engine.py).

- Solo señales con estado "valida" (parser v2). Vicente Luz: todos los tickers que cotizan en
  EE. UU. (docs/ADENDA-01-UNIVERSO.md, retroactivo al 5-oct-2026). Gravity Zone: los 8 tickers.
- Cada corrida recalcula desde las velas guardadas todas las operaciones que aun pueden
  cambiar; una operacion cerrada cuyo seguimiento en sombra ya llego al dia 45 queda congelada.
- Primera corrida de la version 2: el operations.csv anterior (version 1, hecho con señales
  falsas y duplicadas) se copia a data/backup/ y se reconstruye desde cero.
Estados: pending (sin precio aun), sin_precio (5 dias sin velas), open, closed.
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common_utils import (BAR_COLS, DAILY_COLS, DAILY_DIR, GRAVITY_COLS, GRAVITY_CSV,  # noqa: E402
                          OPERATIONS_CSV, OPS_COLS, PRICES_DIR, SIGNALS_COLS, SIGNALS_CSV, backup_once,
                          get_config, load_csv, load_state, log_event, now_utc, save_csv, ticker_file,
                          update_state)
from ops_engine import OPS_VERSION, build_candidates, evaluate  # noqa: E402
from signal_parser import PARSER_VERSION  # noqa: E402


def main() -> int:
    cfg = get_config()
    state = load_state()
    if state.get("parser_version") != PARSER_VERSION:
        log_event("Script 4: esperando el reprocesamiento de señales (parser v2); sin cambios")
        return 0
    now = now_utc()
    signals = load_csv(SIGNALS_CSV, SIGNALS_COLS)
    gravity = load_csv(GRAVITY_CSV, GRAVITY_COLS)
    existing = load_csv(OPERATIONS_CSV, OPS_COLS)
    gz_hist = {o["signal_key"]: o for o in existing if o.get("channel") == "GRAVITY_ZONE"}
    if state.get("ops_version") != OPS_VERSION:
        old_v = state.get("ops_version", 1)
        backup_once(OPERATIONS_CSV, f"operations_v{old_v}_{now:%Y%m%d}.csv")
        log_event(f"Script 4: operaciones v{old_v} respaldadas ({len(existing)}); reconstruyo desde cero")
        existing = []
    by_key = {o["signal_key"]: o for o in existing if o.get("ops_version") == str(OPS_VERSION)}
    next_id = max([int(o["id"]) for o in by_key.values() if o.get("id")] or [0]) + 1

    cache: dict[str, tuple[list, list]] = {}

    def prices(t: str):
        if t not in cache:
            cache[t] = (load_csv(PRICES_DIR / ticker_file(t), BAR_COLS, create=False),
                        load_csv(DAILY_DIR / ticker_file(t), DAILY_COLS, create=False))
        return cache[t]

    ops, frozen, failed = [], 0, 0
    for c in build_candidates(signals, gravity, cfg):
        old = by_key.pop(c["key"], None)
        oid = old["id"] if old else next_id
        if not old:
            next_id += 1
        if old and old["estado"] == "closed" and str(old.get("sombra_completa")) == "1":
            ops.append(old)
            frozen += 1
            continue
        try:
            bars, daily = prices(c["ticker"])
            op = evaluate(c, bars, daily, cfg, now)
        except Exception as e:  # una operacion rara no debe frenar a las demas
            failed += 1
            log_event(f"Script 4: {c['key']} fallo: {type(e).__name__}: {e}", "ERROR")
            op = old or {"ticker": c["ticker"], "option_type": c["type"], "channel": c["channel"],
                         "signal_timestamp": c["ts"], "signal_key": c["key"], "estado": "pending",
                         "ops_version": OPS_VERSION}
        op["id"] = oid
        ops.append(op)
    if not cfg.get("gravity_enabled", True):  # ADENDA-02: el historico de Gravity Zone se conserva tal cual
        used = {int(o["id"]) for o in ops if o.get("id")}
        for o in gz_hist.values():
            if not o.get("id") or int(o["id"]) in used:
                o["id"] = max(used | {0}) + 1   # no chocar con los numeros de Vicente Luz
            used.add(int(o["id"]))
            ops.append(o)
        by_key = {k: o for k, o in by_key.items() if k not in gz_hist}
    if by_key:
        log_event(f"Script 4: {len(by_key)} operaciones sin señal valida (se quitan): "
                  f"{sorted(by_key)[:5]}", "WARNING")
    ops.sort(key=lambda o: int(o["id"]))
    save_csv(OPERATIONS_CSV, OPS_COLS, ops)
    update_state(ops_version=OPS_VERSION)
    states = defaultdict(int)
    for o in ops:
        states[o["estado"]] += 1
    log_event(f"Script 4: {len(ops)} operaciones {dict(states)}; {frozen} congeladas; {failed} con error")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
