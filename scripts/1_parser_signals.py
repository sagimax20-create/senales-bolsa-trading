"""Script 1 — Parser de señales del canal de Telegram "Alertas de Bolsa de Valores Vicente Luz".

Lee mensajes nuevos con Telethon, extrae (ticker, CALL/PUT) de los 8 tickers permitidos y
agrega a data/signals.csv. Solo toma señales con CALL o PUT explicito: acciones "long",
spreads sin direccion clara y promos se ignoran (el protocolo solo prueba CALL/PUT).

Credenciales (variables de entorno / GitHub Secrets):
  TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION (StringSession; ver 0_make_session.py)
Opcional: TELEGRAM_CHANNEL (username o titulo) para sobreescribir config.json.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from datetime import timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common_utils import (SIGNALS_COLS, SIGNALS_CSV, append_csv, fmt_ts, get_config, load_csv,  # noqa: E402
                          load_state, log_event, parse_ts, save_state)

CHANNEL_TAG = "VICENTE_LUZ"
DIRECTION_RE = re.compile(r"\b(CALLS?|PUTS?)\b")
PRICE_RE = re.compile(r"(?:@|\bPRIMA\b|\bPREMIUM\b)\s*:?\s*\$?\s*(\d+(?:\.\d+)?)")


def parse_message(text: str, tickers: list[str]) -> list[dict]:
    """Devuelve [{ticker, signal_type, price_recommended}] de un mensaje.

    Se analiza linea por linea: una linea con ticker + CALL/PUT es una señal. Si la
    linea trae ticker pero no direccion, hereda la del mensaje solo si el mensaje tiene
    UNA sola direccion (si trae CALL y PUT mezclados, es ambiguo y se descarta).
    """
    if not text:
        return []
    upper = text.upper()
    ticker_re = re.compile(r"(?<![A-Z0-9])\$?(" + "|".join(map(re.escape, tickers)) + r")(?![A-Z0-9])")
    msg_dirs = {m.group(1).rstrip("S") for m in DIRECTION_RE.finditer(upper)}
    found: dict[tuple[str, str], str] = {}
    for line in upper.splitlines():
        line_tickers = list(dict.fromkeys(m.group(1) for m in ticker_re.finditer(line)))
        if not line_tickers:
            continue
        line_dirs = {m.group(1).rstrip("S") for m in DIRECTION_RE.finditer(line)}
        dirs = line_dirs or (msg_dirs if len(msg_dirs) == 1 else set())
        if len(dirs) != 1:
            continue
        direction = next(iter(dirs))
        pm = PRICE_RE.search(line)
        for t in line_tickers:
            found.setdefault((t, direction), pm.group(1) if pm else "")
    return [{"ticker": t, "signal_type": d, "price_recommended": p} for (t, d), p in found.items()]


async def _resolve_channel(client, cfg):
    target = os.getenv("TELEGRAM_CHANNEL") or cfg["telegram_channel"]
    try:
        return await client.get_entity(target)
    except Exception as e:
        log_event(f"No pude resolver '{target}' ({e}); busco por titulo en tus dialogos", "WARNING")
    hint = cfg.get("telegram_channel_title_hint", "").lower()
    async for d in client.iter_dialogs():
        if hint and hint in (d.name or "").lower():
            log_event(f"Canal encontrado por titulo: {d.name}")
            return d.entity
    raise RuntimeError("Canal de Telegram no encontrado: revisa telegram_channel en config.json")


async def run() -> int:
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    cfg = get_config()
    api_id, api_hash, session = (os.getenv(k) for k in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_SESSION"))
    if not (api_id and api_hash and session):
        log_event("Faltan TELEGRAM_API_ID / TELEGRAM_API_HASH / TELEGRAM_SESSION; omito Script 1", "ERROR")
        return 1

    tickers = cfg["allowed_tickers"]
    start = parse_ts(cfg["capture_start"] + " 00:00:00")
    state = load_state()
    last_id = int(state.get("telegram_last_message_id", 0))
    existing = {(r["message_id"], r["ticker"], r["signal_type"]) for r in load_csv(SIGNALS_CSV, SIGNALS_COLS)}

    new_rows, max_id = [], last_id
    async with TelegramClient(StringSession(session), int(api_id), api_hash) as client:
        channel = await _resolve_channel(client, cfg)
        async for msg in client.iter_messages(channel, min_id=last_id, limit=int(cfg["telegram_fetch_limit"])):
            max_id = max(max_id, msg.id)
            ts = msg.date.astimezone(timezone.utc)
            if ts < start:
                continue  # el testeo empieza en capture_start; el historial previo no cuenta
            for s in parse_message(msg.message or "", tickers):
                key = (str(msg.id), s["ticker"], s["signal_type"])
                if key in existing:
                    continue
                existing.add(key)
                new_rows.append({
                    "timestamp": fmt_ts(ts), "ticker": s["ticker"], "signal_type": s["signal_type"],
                    "option_type": "OTM", "strike_type": f"{int(cfg['protocol_strike_otm'] * 100)}%",
                    "price_recommended": s["price_recommended"], "channel": CHANNEL_TAG,
                    "message_id": msg.id,
                })
    new_rows.sort(key=lambda r: r["timestamp"])
    n = append_csv(SIGNALS_CSV, SIGNALS_COLS, new_rows)
    if max_id > last_id:  # solo avanza el cursor si la lectura termino bien
        state["telegram_last_message_id"] = max_id
        save_state(state)
    log_event(f"Script 1: {n} señales nuevas (ultimo mensaje leido: {max_id})")
    return 0


def main() -> int:
    try:
        return asyncio.run(run())
    except Exception as e:  # Telegram caido no debe detener el resto del pipeline
        log_event(f"Script 1 fallo: {type(e).__name__}: {e}", "ERROR")
        return 1


if __name__ == "__main__":
    sys.exit(main())
