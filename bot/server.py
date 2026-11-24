"""Servidor del bot de Telegram con comandos (para Render; solo libreria estandar).

Telegram envia cada mensaje del bot a POST /telegram (webhook). El servidor:
  - verifica la cabecera secreta que fija al registrar el webhook (WEBHOOK_SECRET);
  - responde SOLO al chat TELEGRAM_CHAT_ID (el bot es publico: cualquiera puede escribirle);
  - baja los datos publicos del repo (data/*.csv, config.json) como mucho cada 2 minutos y
    contesta con bot/commands.py, que usa las mismas cuentas que los reportes.
Al arrancar registra el webhook en RENDER_EXTERNAL_URL/telegram y la lista de comandos.
Variables de entorno (en Render): TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, WEBHOOK_SECRET,
DATA_BASE_URL (opcional). El token nunca se escribe en el log.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_ROOT = Path(tempfile.mkdtemp(prefix="senales_bot_"))
os.environ["SENALES_ROOT"] = str(DATA_ROOT)          # antes de importar common_utils
sys.path.insert(0, str(HERE.parent / "scripts"))
sys.path.insert(0, str(HERE))

import commands  # noqa: E402
from common_utils import get_config, load_state  # noqa: E402
from report_lib import load_data  # noqa: E402

BASE_URL = os.getenv("DATA_BASE_URL",
                     "https://raw.githubusercontent.com/sagimax20-create/senales-bolsa-trading/main")
FILES = ["config.json", "data/state.json", "data/signals.csv", "data/operations.csv", "data/messages.csv",
         "data/gravity_alerts.csv"]
CACHE_SECONDS = 120
BOT_COMMANDS = [("estado", "Resumen y avance hacia 100 y 300 señales"),
                ("abiertas", "Operaciones abiertas"),
                ("niveles", "Últimos niveles +25/+50/+75/+100%"),
                ("senales", "Últimas señales de Vicente Luz"),
                ("aciertos", "Tickers con más aciertos"),
                ("ayuda", "Lista de comandos")]

_lock = threading.Lock()
_last_refresh = 0.0


def log(msg: str) -> None:
    print(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def refresh_data(force: bool = False) -> None:
    """Baja los archivos del repo a DATA_ROOT (como mucho cada CACHE_SECONDS)."""
    global _last_refresh
    with _lock:
        if not force and time.time() - _last_refresh < CACHE_SECONDS:
            return
        for rel in FILES:
            dest = DATA_ROOT / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                with urllib.request.urlopen(f"{BASE_URL}/{rel}?t={int(time.time())}", timeout=20) as r:
                    dest.write_bytes(r.read())
            except Exception as e:   # se sigue con la copia anterior si la hay
                log(f"no pude bajar {rel}: {type(e).__name__}")
        _last_refresh = time.time()


def webhook_secret() -> str:
    """Valor para la cabecera secreta de Telegram (solo admite A-Z, a-z, 0-9, _ y -): huella
    SHA-256 en hexadecimal de WEBHOOK_SECRET, que Render genera en base64."""
    raw = os.getenv("WEBHOOK_SECRET", "")
    return hashlib.sha256(raw.encode()).hexdigest() if raw else ""


def telegram(method: str, payload: dict) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}",
                                 data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return bool(json.loads(r.read().decode()).get("ok"))
    except urllib.error.HTTPError as e:      # sin URL en el log: llevaria el token
        log(f"Telegram {method}: HTTP {e.code}")
    except Exception as e:
        log(f"Telegram {method}: {type(e).__name__}")
    return False


def reply_for(update: dict) -> dict | None:
    """Respuesta (metodo sendMessage) para un update, o None si no corresponde responder."""
    msg = update.get("message") or {}
    chat_id = str((msg.get("chat") or {}).get("id", ""))
    allowed = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not chat_id or chat_id != allowed:
        return None                            # solo el dueño del bot
    text = msg.get("text") or ""
    refresh_data()
    cfg = get_config()
    answer = commands.respond(text, load_data(cfg), cfg, datetime.now(timezone.utc), load_state())
    return {"method": "sendMessage", "chat_id": chat_id, "text": answer[:4000],
            "disable_web_page_preview": True}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes = b"", ctype: str = "text/plain") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # chequeo de salud de Render
        self._send(200, b"ok")

    def do_POST(self):
        if self.path != "/telegram":
            return self._send(404)
        expected = webhook_secret()
        if not expected or self.headers.get("X-Telegram-Bot-Api-Secret-Token") != expected:
            return self._send(403)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            update = json.loads(self.rfile.read(length) or b"{}")
            reply = reply_for(update)
        except Exception as e:
            log(f"error procesando mensaje: {type(e).__name__}: {e}")
            reply = None
        if reply:   # se responde en el mismo webhook: Telegram ejecuta el metodo
            return self._send(200, json.dumps(reply).encode(), "application/json")
        return self._send(200)

    def log_message(self, fmt, *args):  # sin log por pedido
        return


def setup_webhook() -> None:
    url = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
    if not (os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("WEBHOOK_SECRET") and url):
        log("falta TELEGRAM_BOT_TOKEN, WEBHOOK_SECRET o RENDER_EXTERNAL_URL: no registro el webhook")
        return
    ok = telegram("setWebhook", {"url": f"{url}/telegram", "secret_token": webhook_secret(),
                                 "allowed_updates": ["message"], "drop_pending_updates": True})
    telegram("setMyCommands", {"commands": [{"command": c, "description": d} for c, d in BOT_COMMANDS]})
    log(f"webhook {'registrado' if ok else 'NO registrado'} en {url}/telegram")
    if not os.getenv("TELEGRAM_CHAT_ID"):
        log("falta TELEGRAM_CHAT_ID: el bot no respondera a nadie")


def main() -> None:
    threading.Thread(target=lambda: (refresh_data(force=True), setup_webhook()), daemon=True).start()
    port = int(os.getenv("PORT", "10000"))
    log(f"escuchando en el puerto {port}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
