"""Funciones compartidas del pipeline de testeo de señales (ver PROTOCOLO.md).

Decisiones de diseño:
- Solo libreria estandar + yfinance/telethon (sin pandas): los CSV son pequeños y
  asi el nucleo de calculo se puede probar sin instalar nada.
- Todas las marcas de tiempo son UTC, formato "YYYY-MM-DD HH:MM:SS".
- Los CSV son append-only salvo operations.csv, que se reescribe (con respaldo)
  porque las operaciones abiertas cambian de estado.
"""
from __future__ import annotations

import csv
import json
import logging
import math
import os
import shutil
import smtplib
import statistics
import sys
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

try:  # .env es opcional (en GitHub Actions se usan Secrets)
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass

ROOT = Path(os.environ.get("SENALES_ROOT") or Path(__file__).resolve().parent.parent)
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"
BACKUP_DIR = DATA_DIR / "backup"
CONFIG_PATH = ROOT / "config.json"
LOG_PATH = DATA_DIR / "log.txt"
STATE_PATH = DATA_DIR / "state.json"

SIGNALS_CSV = DATA_DIR / "signals.csv"
GRAVITY_CSV = DATA_DIR / "gravity_alerts.csv"
PRICES_CSV = DATA_DIR / "daily_prices.csv"
OPERATIONS_CSV = DATA_DIR / "operations.csv"

# Las primeras columnas de cada archivo son las del prompt; las extras van al final.
SIGNALS_COLS = ["timestamp", "ticker", "signal_type", "option_type", "strike_type",
                "price_recommended", "channel", "message_id"]
GRAVITY_COLS = ["timestamp", "ticker", "signal_type", "gz_level", "price_spot", "channel"]
PRICES_COLS = ["timestamp", "ticker", "price_spot", "price_high", "price_low",
               "volatility_pct", "iv_estimate"]
OPS_COLS = ["id", "timestamp_entry", "ticker", "option_type", "strike_usd", "price_entry",
            "price_exit", "reason_exit", "days_held", "retorno_bruto_pct", "comision_pct",
            "retorno_neto_pct", "estado",
            # extras: trazabilidad y analisis retrospectivo (PROTOCOLO nota 2)
            "channel", "signal_timestamp", "spot_entry", "iv_entry", "expiry",
            "timestamp_exit", "peak_return_pct", "trough_return_pct", "signal_key"]

DEFAULT_CONFIG = {
    "allowed_tickers": ["GDX", "NVDA", "MARA", "GLD", "INTC", "MSFT", "DIS", "FCX"],
    "protocol_capital_per_signal": 1000,
    "protocol_strike_otm": 0.95,
    "protocol_commission": 0.01,
    "protocol_take_profit": 0.50,
    "protocol_stop_loss": -0.50,
    "protocol_days_expiry": 45,
    "protocol_hito_alarm": 100,
    "protocol_hito_decision": 300,
    "protocol_min_return": 0.10,
    "protocol_bank_cost_entry": 0.08,
    "protocol_bank_cost_exit": 0.10,
    "capture_start": "2026-10-04",
    "telegram_channel": "alertas_bolsa_vicente_luz",
    "telegram_channel_title_hint": "Vicente Luz",
    "telegram_fetch_limit": 200,
    "risk_free_rate": 0.045,
    "gravity_call_level": -2.0,
    "gravity_put_level": 2.0,
    "gravity_lookback_days": 20,
    "gravity_dedupe_hours": 24,
    "gravity_vol_compression_call": 1.5,
    "gravity_vol_expansion_put": 3.5,
    "alert_return_threshold": 0.05,
    "iv_fallback": {
        "GDX": 0.34,
        "NVDA": 0.36,
        "MARA": 0.45,
        "GLD": 0.23,
        "INTC": 0.30,
        "MSFT": 0.25,
        "DIS": 0.27,
        "FCX": 0.35,
    },
}

NY = ZoneInfo("America/New_York")

# --------------------------------------------------------------------------- logging
_logger: logging.Logger | None = None


def _get_logger() -> logging.Logger:
    global _logger
    if _logger is None:
        _logger = logging.getLogger("senales")
        _logger.setLevel(logging.INFO)
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        _logger.addHandler(sh)
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
            fh.setFormatter(fmt)
            _logger.addHandler(fh)
        except OSError:
            pass
    return _logger


def log_event(message: str, level: str = "INFO") -> None:
    """Registra un evento en consola y en data/log.txt."""
    getattr(_get_logger(), level.lower(), _get_logger().info)(message)


# --------------------------------------------------------------------------- config / estado
def get_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except FileNotFoundError:
        log_event("config.json no existe; uso valores por defecto", "WARNING")
    return cfg


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, STATE_PATH)


# --------------------------------------------------------------------------- tiempo
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def fmt_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def parse_ts(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def market_open(now: datetime | None = None) -> bool:
    """NYSE lun-vie 09:30-16:00 hora de Nueva York (maneja EDT/EST solo).
    No conoce feriados: ahi lo cubre la comprobacion de fecha de la ultima vela."""
    ny = (now or now_utc()).astimezone(NY)
    if ny.weekday() >= 5:
        return False
    minutes = ny.hour * 60 + ny.minute
    return 9 * 60 + 30 <= minutes < 16 * 60


# --------------------------------------------------------------------------- CSV
def _ensure(path: Path, columns: list[str]) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(columns)


def load_csv(path: Path, columns: list[str]) -> list[dict]:
    """Carga un CSV como lista de dicts; lo crea (solo encabezado) si no existe."""
    path = Path(path)
    _ensure(path, columns)
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:  # tolera archivos con menos columnas (esquema que crecio)
        for c in columns:
            r.setdefault(c, "")
    return rows


def append_csv(path: Path, columns: list[str], rows: list[dict]) -> int:
    """Agrega filas sin tocar las existentes. Devuelve cuantas escribio."""
    path = Path(path)
    _ensure(path, columns)
    if not rows:
        return 0
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore", restval="")
        w.writerows(rows)
    return len(rows)


def save_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    """Reescribe un CSV de forma atomica, guardando antes copia en data/backup/."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, BACKUP_DIR / (path.name + ".bak"))
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore", restval="")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def to_float(x, default: float | None = None) -> float | None:
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- opciones
def calculate_strike(spot_price: float, otm_pct: float = 0.95, option_type: str = "CALL") -> float:
    """Strike OTM segun PROTOCOLO: CALL 5% arriba del spot, PUT 5% abajo."""
    gap = 1 - otm_pct
    strike = spot_price * (1 + gap) if option_type.upper() == "CALL" else spot_price * (1 - gap)
    return round(strike, 2)


def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(spot: float, strike: float, t_years: float, sigma: float, r: float,
             option_type: str) -> float:
    """Prima teorica Black-Scholes. Es un MODELO: no hay cotizaciones reales de
    opciones en el pipeline (ver 'supuestos' en los reportes)."""
    is_call = option_type.upper() == "CALL"
    if t_years <= 0 or sigma <= 0:
        return max(spot - strike, 0.0) if is_call else max(strike - spot, 0.0)
    srt = sigma * math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma ** 2) * t_years) / srt
    d2 = d1 - srt
    disc = math.exp(-r * t_years)
    if is_call:
        return spot * _ncdf(d1) - strike * disc * _ncdf(d2)
    return strike * disc * _ncdf(-d2) - spot * _ncdf(-d1)


def realized_vol(closes: list[float], window: int = 20) -> float | None:
    """Volatilidad realizada anualizada (log-retornos diarios, ultimos `window`)."""
    c = closes[-(window + 1):]
    rets = [math.log(b / a) for a, b in zip(c, c[1:]) if a > 0 and b > 0]
    if len(rets) < 10:
        return None
    return statistics.stdev(rets) * math.sqrt(252)


def iv_for(ticker: str, closes: list[float], cfg: dict) -> float:
    """IV estimada = vol realizada 20d; si no hay historia, punto medio de la tabla
    de TICKERS-PARA-SEGUIMIENTO-GRAVITY.md. Acotada a [0.10, 1.50]."""
    rv = realized_vol(closes)
    if rv is None:
        rv = float(cfg.get("iv_fallback", {}).get(ticker, 0.30))
    return min(max(rv, 0.10), 1.50)


# --------------------------------------------------------------------------- mercado
def get_daily_history(ticker: str, period: str = "3mo") -> list[dict]:
    """Velas diarias de yfinance: [{date, high, low, close}]. La ultima puede ser la
    del dia en curso (close = precio actual). Lanza excepcion si falla."""
    import yfinance as yf  # import tardio: los scripts de calculo no lo necesitan

    df = yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=False)
    out = []
    for idx, row in df.iterrows():
        close, high, low = to_float(row.get("Close")), to_float(row.get("High")), to_float(row.get("Low"))
        if close is None or high is None or low is None:
            continue
        out.append({"date": idx.date(), "high": high, "low": low, "close": close})
    if not out:
        raise RuntimeError(f"yfinance devolvio 0 velas para {ticker}")
    return out


def send_email_alert(subject: str, body: str) -> bool:
    """Opcional. Requiere SMTP_HOST, SMTP_USER, SMTP_PASS y ALERT_EMAIL_TO; sin ellos no hace nada."""
    host, user, pwd, to = (os.getenv(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "ALERT_EMAIL_TO"))
    if not all((host, user, pwd, to)):
        return False
    try:
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = subject, user, to
        msg.set_content(body)
        with smtplib.SMTP_SSL(host, int(os.getenv("SMTP_PORT", "465")), timeout=20) as s:
            s.login(user, pwd)
            s.send_message(msg)
        return True
    except Exception as e:  # una alerta fallida nunca debe tumbar el pipeline
        log_event(f"send_email_alert fallo: {e}", "WARNING")
        return False
