"""Script 0 — Actualiza la lista de simbolos que cotizan en EE. UU. (data/ref/us_symbols.csv).

Fuente oficial: Nasdaq Trader, archivo nasdaqtraded.txt (todas las bolsas de EE. UU., con
marca de ETF). Se descarga como mucho una vez cada `symbols_max_age_days` dias. Si la
descarga falla o el archivo parece incompleto, se conserva la lista actual: este script nunca
deja al parser sin lista. `--force` descarga aunque la lista sea reciente.

El parser (scripts/signal_parser.py) solo acepta como ticker un simbolo de esta lista.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
import urllib.request
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common_utils import (SYMBOLS_CSV, SYMBOLS_META, fmt_ts, get_config, load_state,  # noqa: E402
                          log_event, now_utc, parse_ts, save_state)

URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"
SYMBOL_OK = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")
MIN_SYMBOLS = 5000  # menos que esto = descarga incompleta


def parse_nasdaqtraded(text: str) -> tuple[list[dict], str]:
    """Devuelve (filas {symbol, etf, fuente}, fecha de creacion del archivo)."""
    created = ""
    lines = text.splitlines()
    if lines and lines[-1].startswith("File Creation Time"):
        created = lines.pop().split(":", 1)[1].strip()
    rows = {}
    for r in csv.DictReader(io.StringIO("\n".join(lines)), delimiter="|"):
        if (r.get("Test Issue") or "N") != "N":
            continue
        sym = (r.get("Symbol") or "").strip().upper().replace(" ", ".")
        if SYMBOL_OK.match(sym):
            rows[sym] = {"symbol": sym, "etf": "Y" if r.get("ETF") == "Y" else "N",
                         "fuente": "nasdaqtrader:nasdaqtraded.txt"}
    return [rows[s] for s in sorted(rows)], created


def main() -> int:
    cfg = get_config()
    state = load_state()
    max_age = timedelta(days=float(cfg.get("symbols_max_age_days", 7)))
    last = state.get("symbols_updated_at")
    if "--force" not in sys.argv and SYMBOLS_CSV.exists() and last and now_utc() - parse_ts(last) < max_age:
        log_event("Script 0: lista de simbolos al dia; sin descarga")
        return 0
    try:
        req = urllib.request.Request(URL, headers={"User-Agent": "senales-bolsa-trading/2"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = resp.read().decode("utf-8", errors="replace")
        rows, created = parse_nasdaqtraded(text)
        if len(rows) < MIN_SYMBOLS:
            raise RuntimeError(f"solo {len(rows)} simbolos; se conserva la lista actual")
    except Exception as e:  # sin red o archivo raro: seguir con la lista versionada
        log_event(f"Script 0: no pude actualizar simbolos ({type(e).__name__}: {e})", "WARNING")
        return 0
    SYMBOLS_CSV.parent.mkdir(parents=True, exist_ok=True)
    tmp = SYMBOLS_CSV.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["symbol", "etf", "fuente"])
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, SYMBOLS_CSV)
    SYMBOLS_META.write_text(json.dumps({
        "fuente": URL, "descargado": fmt_ts(now_utc()), "file_creation_time": created or "sin dato",
        "n_simbolos": len(rows), "n_etf": sum(r["etf"] == "Y" for r in rows),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    state["symbols_updated_at"] = fmt_ts(now_utc())
    save_state(state)
    log_event(f"Script 0: lista de simbolos actualizada ({len(rows)} simbolos)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
