"""Script 1 — Señales del canal de Telegram "Alertas de Bolsa de Valores Vicente Luz".

Lee los mensajes con Telethon EN ORDEN CRONOLOGICO (sin limite de 200: no se pierde ninguno
aunque GitHub se salte corridas) y aplica el parser v2 (scripts/signal_parser.py).

Archivos (el texto de los mensajes NO se guarda: el repo es publico; solo su SHA-256):
  data/signals.csv        una fila por señal candidata: estado valida/descartada + motivo
  data/messages.csv       una fila por mensaje leido desde capture_start (cobertura)
  data/signals_v1_audit.csv  cada fila de la version 1 marcada valida/descartada con motivo

Reprocesamiento: si state.json no tiene parser_version == 2 (primera corrida despues del
cambio), o con --reprocess / REPROCESS=1, se releen TODOS los mensajes desde capture_start y se
reconstruyen los archivos. Antes se copia el signals.csv anterior a data/backup/ (nunca se
borra nada). Si la lectura falla o queda incompleta, no se escribe nada.

Credenciales (variables de entorno / GitHub Secrets):
  TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION (StringSession; ver 0_make_session.py)
Opcional: TELEGRAM_CHANNEL (username o titulo) para sobreescribir config.json.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common_utils import (AUDIT_V1_COLS, AUDIT_V1_CSV, BACKUP_DIR, DAILY_COLS, DAILY_DIR,  # noqa: E402
                          MESSAGES_COLS, MESSAGES_CSV, NY, SIGNALS_COLS, SIGNALS_CSV, SIGNALS_V1_COLS,
                          append_csv, backup_once, csv_header, fmt_ts, get_config, get_daily_history,
                          load_csv, load_state, load_symbols, log_event, now_utc, parse_ts,
                          sanitize_log_file, save_csv, ticker_file, to_float, update_state)
from signal_parser import (PARSER_VERSION, audit_v1_row, mark_reposts, message_motivo,  # noqa: E402
                           parse_message, text_sha256)

# Un ticker escrito en minusculas solo se acepta si el strike publicado es coherente con el
# precio real de ese dia (evita leer "Grab Jan 50 calls" como el ticker GRAB, que vale ~$5).
STRIKE_SPOT_RANGE = (0.5, 2.0)


def verify_lowercase(row: dict, spot_fn) -> str:
    """'' si la señal con ticker en minusculas es coherente; si no, el motivo de descarte."""
    strike = to_float(row.get("strike_vl"))
    if strike is None:
        return "minusculas_sin_strike"
    spot = spot_fn(row["ticker"], parse_ts(row["timestamp"]).astimezone(NY).date()) if spot_fn else None
    if not spot:
        return "minusculas_sin_precio"
    lo, hi = STRIKE_SPOT_RANGE
    return "" if lo <= strike / spot <= hi else "minusculas_strike_incoherente"


def make_spot_lookup():
    """Cierre diario del ticker en una fecha (ultimo cierre anterior si no hubo sesion). Usa
    data/prices_1d/ y descarga de Yahoo si falta el tramo; guarda lo descargado."""
    cache: dict[str, list[dict]] = {}

    def spot(ticker: str, d: date) -> float | None:
        iso = d.isoformat()
        if ticker not in cache:
            path = DAILY_DIR / ticker_file(ticker)
            rows = load_csv(path, DAILY_COLS, create=False)
            stale = not rows or rows[0]["date"] > iso or (d - date.fromisoformat(rows[-1]["date"])).days > 4
            if stale:
                try:
                    new = [{"date": h["date"].isoformat(), **{k: round(h[k], 4) for k in ("open", "high", "low", "close")}}
                           for h in get_daily_history(ticker, "1y")]
                    by = {r["date"]: r for r in rows}
                    by.update({r["date"]: r for r in new})
                    rows = [by[k] for k in sorted(by)]
                    save_csv(path, DAILY_COLS, rows, backup=False)
                except Exception as e:
                    log_event(f"Script 1: sin precio diario de {ticker} ({type(e).__name__})", "WARNING")
            cache[ticker] = rows
        prior = [r for r in cache[ticker] if r["date"] <= iso]
        return to_float(prior[-1]["close"]) if prior else None

    return spot

CHANNEL_TAG = "VICENTE_LUZ"


def build_rows(messages: list[tuple[int, datetime, str]], symbols: set[str], cfg: dict,
               history: list[dict], spot_fn=None) -> tuple[list[dict], list[dict], dict[int, list[dict]]]:
    """Aplica el parser a mensajes (id, fecha UTC, texto) en orden, verifica los tickers escritos
    en minusculas contra el precio (spot_fn) y marca reposteos.

    Devuelve (filas de signals.csv, filas de messages.csv, candidatas por message_id)."""
    allowed = set(cfg["allowed_tickers"])
    strike_type = f"{int(round(cfg['protocol_strike_otm'] * 100))}%"
    sig_rows, msg_rows, per_msg = [], [], {}
    for mid, ts, text in sorted(messages, key=lambda m: m[0]):
        cands = parse_message(text, symbols, ts.astimezone(NY).date())
        rows = []
        for c in cands:
            rows.append({
                "timestamp": fmt_ts(ts), "ticker": c.ticker, "signal_type": c.direction,
                "option_type": "OTM", "strike_type": strike_type,
                "price_recommended": "" if c.premium is None else f"{c.premium:g}",
                "channel": CHANNEL_TAG, "message_id": str(mid),
                "signal_id": f"VL-{mid}-{c.ticker}-{c.direction[0]}",
                "estado": c.estado, "motivo": c.motivo, "repite_a": "",
                "strike_vl": "" if c.strike is None else f"{c.strike:g}", "expiry_vl": c.expiry,
                "linea": str(c.line_no), "en_universo_original": "1" if c.ticker in allowed else "0",
                "parser_version": str(PARSER_VERSION), "ticker_escrito": c.escrito, "spread": c.spread,
            })
        per_msg[mid] = rows
        sig_rows += rows
        msg_rows.append({"message_id": str(mid), "timestamp": fmt_ts(ts), "sha256": text_sha256(text),
                         "n_caracteres": len(text or ""), "n_candidatas": len(rows),
                         "parser_version": str(PARSER_VERSION), "_text": text, "_cands": cands})
    for r in sig_rows:
        if r["ticker_escrito"] == "minusculas" and r["estado"] == "valida":
            motivo = verify_lowercase(r, spot_fn)
            if motivo:
                r["estado"], r["motivo"] = "descartada", motivo
    mark_reposts(sig_rows, history, float(cfg.get("repost_window_days", 45)))
    for m in msg_rows:  # contar validas DESPUES de marcar reposteos
        text, cands = m.pop("_text"), m.pop("_cands")
        rows = per_msg[int(m["message_id"])]
        m["n_validas"] = sum(r["estado"] == "valida" for r in rows)
        if rows:
            m["motivo"] = "" if m["n_validas"] else "solo_descartadas"
        else:
            m["motivo"] = message_motivo(text, cands)
    return sig_rows, msg_rows, per_msg


def reprocess(messages: list[tuple[int, datetime, str]], symbols: set[str], cfg: dict,
              old_rows: list[dict], spot_fn=None) -> tuple[list[dict], list[dict], list[dict]]:
    """Reconstruye signals.csv y messages.csv desde cero y audita las filas de la version 1."""
    sig_rows, msg_rows, per_msg = build_rows(messages, symbols, cfg, history=[], spot_fn=spot_fn)
    texts = {mid: text for mid, _, text in messages}
    audit = []
    for old in old_rows:
        mid = int(old["message_id"])
        estado, motivo, sid = audit_v1_row(old, texts.get(mid), per_msg.get(mid, []), symbols)
        audit.append({**{c: old.get(c, "") for c in AUDIT_V1_COLS[:8]},
                      "estado_v2": estado, "motivo_v2": motivo, "signal_id_v2": sid})
    return sig_rows, msg_rows, audit


# --------------------------------------------------------------------------- Telegram
async def _resolve_channel(client, cfg):
    target = os.getenv("TELEGRAM_CHANNEL") or cfg["telegram_channel"]
    try:
        return await client.get_entity(target)
    except Exception as e:
        log_event(f"No pude resolver '{target}' ({type(e).__name__}); busco por titulo en tus dialogos", "WARNING")
    hint = cfg.get("telegram_channel_title_hint", "").lower()
    async for d in client.iter_dialogs():
        if hint and hint in (d.name or "").lower():
            log_event(f"Canal encontrado por titulo: {d.name}")
            return d.entity
    raise RuntimeError("Canal de Telegram no encontrado: revisa telegram_channel en config.json")


def _as_tuple(msg) -> tuple[int, datetime, str] | None:
    if msg is None or getattr(msg, "date", None) is None:
        return None
    return msg.id, msg.date.astimezone(timezone.utc), getattr(msg, "message", "") or ""


async def _fetch(cfg, start: datetime, last_id: int, full: bool, extra_ids: set[int]):
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    api_id, api_hash, session = (os.getenv(k) for k in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_SESSION"))
    out = {}
    async with TelegramClient(StringSession(session), int(api_id), api_hash) as client:
        channel = await _resolve_channel(client, cfg)
        if full:  # reverse=True: del mas antiguo al mas nuevo, desde capture_start
            it = client.iter_messages(channel, offset_date=start, reverse=True)
        else:     # reverse=True + min_id: solo mensajes con id > last_id, en orden
            it = client.iter_messages(channel, min_id=last_id, reverse=True)
        async for msg in it:
            t = _as_tuple(msg)
            if t:
                out[t[0]] = t
        missing = sorted(i for i in extra_ids if i not in out)
        if missing:  # mensajes de la v1 que no aparecieron: pedirlos por id
            for msg in await client.get_messages(channel, ids=missing):
                t = _as_tuple(msg)
                if t:
                    out[t[0]] = t
    return list(out.values())


def run() -> int:
    cfg = get_config()
    if not all(os.getenv(k) for k in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_SESSION")):
        log_event("Faltan TELEGRAM_API_ID / TELEGRAM_API_HASH / TELEGRAM_SESSION; omito Script 1", "ERROR")
        return 1
    symbols = load_symbols()
    if len(symbols) < 1000:
        log_event("Script 1: lista de simbolos ausente o incompleta (data/ref/us_symbols.csv)", "ERROR")
        return 1
    state = load_state()
    start = parse_ts(cfg["capture_start"] + " 00:00:00")
    last_id = int(state.get("telegram_last_message_id", 0))
    full = (state.get("parser_version") != PARSER_VERSION or os.getenv("REPROCESS") == "1"
            or "--reprocess" in sys.argv)

    old_rows, old_version = [], state.get("parser_version", 1)
    if full:
        old_rows = load_csv(SIGNALS_CSV, SIGNALS_V1_COLS, create=False)
        if "estado" in csv_header(SIGNALS_CSV):  # ya no es v1: auditar contra la copia de la v1
            v1 = sorted(BACKUP_DIR.glob("signals_v1_*.csv"))
            old_rows = load_csv(v1[0], SIGNALS_V1_COLS, create=False) if v1 else []
    old_ids = {int(r["message_id"]) for r in old_rows if r.get("message_id")}

    messages = asyncio.run(_fetch(cfg, start, last_id, full, old_ids if full else set()))
    messages = [m for m in messages if m[1] >= start]

    if full:
        seen = {m[0] for m in messages}
        if not messages or (old_ids and len(old_ids & seen) < 0.5 * len(old_ids)):
            log_event(f"Script 1: reprocesamiento abortado: lei {len(messages)} mensajes y solo "
                      f"{len(old_ids & seen)}/{len(old_ids)} de la version anterior; no escribo nada", "ERROR")
            return 1
        stamp = now_utc().strftime("%Y%m%d")
        backup_once(SIGNALS_CSV, f"signals_v{old_version}_{stamp}.csv")
        backup_once(MESSAGES_CSV, f"messages_v{old_version}_{stamp}.csv")
        sig_rows, msg_rows, audit = reprocess(messages, symbols, cfg, old_rows, make_spot_lookup())
        save_csv(SIGNALS_CSV, SIGNALS_COLS, sig_rows)
        save_csv(MESSAGES_CSV, MESSAGES_COLS, msg_rows)
        if audit:
            save_csv(AUDIT_V1_CSV, AUDIT_V1_COLS, audit)
        masked = sanitize_log_file()
        valid = sum(r["estado"] == "valida" for r in sig_rows)
        update_state(parser_version=PARSER_VERSION, reprocessed_at=fmt_ts(now_utc()),
                     telegram_last_message_id=max([last_id] + [m[0] for m in messages]),
                     reprocess_stats={"mensajes": len(msg_rows), "candidatas": len(sig_rows),
                                      "validas": valid, "filas_v1": len(old_rows),
                                      "filas_v1_validas": sum(a["estado_v2"] == "valida" for a in audit),
                                      "correos_enmascarados_log": masked})
        log_event(f"Script 1: REPROCESADO parser v{PARSER_VERSION}: {len(msg_rows)} mensajes, "
                  f"{len(sig_rows)} candidatas, {valid} validas; {len(old_rows)} filas v1 auditadas")
        return 0

    history = load_csv(SIGNALS_CSV, SIGNALS_COLS)
    known = {r["message_id"] for r in load_csv(MESSAGES_CSV, MESSAGES_COLS)}
    messages = [m for m in messages if str(m[0]) not in known]
    sig_rows, msg_rows, _ = build_rows(messages, symbols, cfg, history, make_spot_lookup())
    append_csv(SIGNALS_CSV, SIGNALS_COLS, sig_rows)
    append_csv(MESSAGES_CSV, MESSAGES_COLS, msg_rows)
    if messages:
        update_state(telegram_last_message_id=max([last_id] + [m[0] for m in messages]))
    valid = sum(r["estado"] == "valida" for r in sig_rows)
    log_event(f"Script 1: {len(messages)} mensajes nuevos, {valid} señales validas "
              f"({len(sig_rows) - valid} descartadas)")
    return 0


def main() -> int:
    try:
        return run()
    except Exception as e:  # Telegram caido no debe detener el resto del pipeline
        log_event(f"Script 1 fallo: {type(e).__name__}: {e}", "ERROR")
        return 1


if __name__ == "__main__":
    sys.exit(main())
