"""Funciones compartidas del pipeline de testeo de señales (ver PROTOCOLO.md).

Decisiones de diseño:
- Solo libreria estandar + yfinance/telethon (sin pandas): los CSV son pequeños y
  asi el nucleo de calculo se puede probar sin instalar nada.
- Todas las marcas de tiempo son UTC, formato "YYYY-MM-DD HH:MM:SS".
- Los CSV se reescriben de forma atomica y con respaldo en data/backup/.
- El log nunca guarda direcciones de correo (el repo es publico): se enmascaran.

El protocolo congelado no esta en el repo (contiene datos personales); su huella SHA-256
esta en PROTOCOLO.sha256. Cambios aprobados: docs/ADENDA-01-UNIVERSO.md. Detalles de
implementacion e interpretaciones: docs/BITACORA-IMPLEMENTACION.md.
"""
from __future__ import annotations

import csv
import json
import logging
import math
import os
import re
import shutil
import smtplib
import statistics
import sys
from datetime import date, datetime, timezone
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
STATE_PATH = DATA_DIR / "state.json"                # lo escribe solo el monitor
REPORT_STATE_PATH = DATA_DIR / "report_state.json"  # lo escriben solo los reportes semanal y mensual

SIGNALS_CSV = DATA_DIR / "signals.csv"
MESSAGES_CSV = DATA_DIR / "messages.csv"
AUDIT_V1_CSV = DATA_DIR / "signals_v1_audit.csv"
GRAVITY_CSV = DATA_DIR / "gravity_alerts.csv"
PRICES_CSV = DATA_DIR / "daily_prices.csv"  # v1: capturas sueltas; ya no se escribe (historico)
PRICES_DIR = DATA_DIR / "prices"            # v2: velas de 15 min (o 60 min) por ticker
DAILY_DIR = DATA_DIR / "prices_1d"          # v2: velas diarias por ticker (volatilidad 20d)
OPERATIONS_CSV = DATA_DIR / "operations.csv"
SYMBOLS_CSV = DATA_DIR / "ref" / "us_symbols.csv"
SYMBOLS_META = DATA_DIR / "ref" / "us_symbols.meta.json"

# Las primeras 8 columnas son las de la version 1 (compatibilidad); el resto es v2.
SIGNALS_V1_COLS = ["timestamp", "ticker", "signal_type", "option_type", "strike_type",
                   "price_recommended", "channel", "message_id"]
SIGNALS_COLS = SIGNALS_V1_COLS + [
    "signal_id", "estado", "motivo", "repite_a", "strike_vl", "expiry_vl", "linea",
    "en_universo_original", "parser_version", "ticker_escrito"]
MESSAGES_COLS = ["message_id", "timestamp", "sha256", "n_caracteres", "n_candidatas", "n_validas",
                 "motivo", "parser_version"]
AUDIT_V1_COLS = SIGNALS_V1_COLS + ["estado_v2", "motivo_v2", "signal_id_v2"]
GRAVITY_COLS = ["timestamp", "ticker", "signal_type", "gz_level", "price_spot", "channel"]
PRICES_COLS = ["timestamp", "ticker", "price_spot", "price_high", "price_low",
               "volatility_pct", "iv_estimate"]
BAR_COLS = ["timestamp", "open", "high", "low", "close", "volume", "interval"]
DAILY_COLS = ["date", "open", "high", "low", "close"]
OPS_COLS = ["id", "timestamp_entry", "ticker", "option_type", "strike_usd", "price_entry",
            "price_exit", "reason_exit", "days_held", "retorno_bruto_pct", "comision_pct",
            "retorno_neto_pct", "estado",
            # trazabilidad
            "channel", "signal_timestamp", "spot_entry", "iv_entry", "expiry",
            "timestamp_exit", "peak_return_pct", "trough_return_pct", "signal_key",
            # v2
            "signal_id", "message_id", "en_universo_original", "spot_exit", "bar_interval",
            # PROTOCOLO §10 nota 2: seguimiento en sombra hasta el dia 45 (no altera la regla)
            "peak_45d_pct", "trough_45d_pct", "toco_100", "toco_150", "toco_200",
            "ret_sin_reglas_pct", "sombra_completa", "ops_version"]

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
    "vicente_universe": "us_listed",
    "repost_window_days": 45,
    "symbols_max_age_days": 7,
    "price_offhours_min_interval_hours": 4,
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

# Feriados NYSE (mercado cerrado todo el dia). Fuente: calendario publicado por NYSE.
NYSE_HOLIDAYS = {
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3), date(2026, 5, 25),
    date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25),
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26), date(2027, 5, 31),
    date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6), date(2027, 11, 25), date(2027, 12, 24),
}

# Opciones sobre indices de Cboe: el subyacente no es una accion, se mapea a Yahoo.
# valor = (simbolo en Yahoo, factor). XSP es el mini-SPX: 1/10 del indice.
INDEX_UNDERLYINGS = {"SPX": ("^GSPC", 1.0), "XSP": ("^GSPC", 0.1), "NDX": ("^NDX", 1.0),
                     "RUT": ("^RUT", 1.0)}

# --------------------------------------------------------------------------- logging
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")


def mask_emails(text: str) -> str:
    return _EMAIL_RE.sub("[OCULTO]", text)


class _MaskEmails(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = mask_emails(str(record.msg))
        return True


_logger: logging.Logger | None = None


def _get_logger() -> logging.Logger:
    global _logger
    if _logger is None:
        _logger = logging.getLogger("senales")
        _logger.setLevel(logging.INFO)
        _logger.addFilter(_MaskEmails())
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
    """Registra un evento en consola y en data/log.txt (con correos enmascarados)."""
    getattr(_get_logger(), level.lower(), _get_logger().info)(message)


def sanitize_log_file(path: Path = LOG_PATH) -> int:
    """Enmascara correos ya escritos en el log. Reescribe en el mismo archivo (no lo
    reemplaza) para no romper un FileHandler abierto. Devuelve cuantos reemplazo."""
    if not path.exists():
        return 0
    with open(path, "r+", encoding="utf-8") as f:
        text = f.read()
        clean, n = _EMAIL_RE.subn("[OCULTO]", text)
        if n:
            f.seek(0)
            f.write(clean)
            f.truncate()
    return n


# --------------------------------------------------------------------------- config / estado
def get_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except FileNotFoundError:
        log_event("config.json no existe; uso valores por defecto", "WARNING")
    return cfg


def load_state(path: Path = STATE_PATH) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict, path: Path = STATE_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def update_state(path: Path = STATE_PATH, **changes) -> dict:
    """Relee el archivo de estado y aplica cambios: evita pisar claves de otro script."""
    state = load_state(path)
    state.update(changes)
    save_state(state, path)
    return state


# --------------------------------------------------------------------------- tiempo
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def fmt_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def parse_ts(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in NYSE_HOLIDAYS


def market_open(now: datetime | None = None) -> bool:
    """NYSE lun-vie 09:30-16:00 hora de Nueva York, sin feriados (maneja EDT/EST solo)."""
    ny = (now or now_utc()).astimezone(NY)
    if not is_trading_day(ny.date()):
        return False
    minutes = ny.hour * 60 + ny.minute
    return 9 * 60 + 30 <= minutes < 16 * 60


# --------------------------------------------------------------------------- CSV
def _ensure(path: Path, columns: list[str]) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(columns)


def csv_header(path: Path) -> list[str]:
    try:
        with open(path, newline="", encoding="utf-8") as f:
            return next(csv.reader(f), [])
    except FileNotFoundError:
        return []


def load_csv(path: Path, columns: list[str], create: bool = True) -> list[dict]:
    """Carga un CSV como lista de dicts; lo crea (solo encabezado) si no existe."""
    path = Path(path)
    if not path.exists():
        if not create:
            return []
        _ensure(path, columns)
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:  # tolera archivos con menos columnas (esquema que crecio)
        for c in columns:
            if r.get(c) is None:
                r[c] = ""
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


def save_csv(path: Path, columns: list[str], rows: list[dict], backup: bool = True) -> None:
    """Reescribe un CSV de forma atomica, guardando antes copia en data/backup/."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, BACKUP_DIR / (path.name + ".bak"))
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore", restval="")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def backup_once(path: Path, name: str) -> Path | None:
    """Copia `path` a data/backup/<name> si esa copia aun no existe (nunca la pisa)."""
    if not path.exists():
        return None
    dest = BACKUP_DIR / name
    if not dest.exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
    return dest


def to_float(x, default: float | None = None) -> float | None:
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- simbolos
def load_symbols(path: Path = SYMBOLS_CSV) -> set[str]:
    """Simbolos que cotizan en EE. UU. (acciones y ETF) + subyacentes de indices de Cboe."""
    try:
        with open(path, newline="", encoding="utf-8") as f:
            syms = {r["symbol"].strip().upper() for r in csv.DictReader(f) if r.get("symbol")}
    except FileNotFoundError:
        return set()
    return syms | set(INDEX_UNDERLYINGS)


def yahoo_symbol(ticker: str) -> tuple[str, float]:
    """Simbolo de Yahoo y factor de escala para un ticker del canal."""
    if ticker in INDEX_UNDERLYINGS:
        return INDEX_UNDERLYINGS[ticker]
    return ticker.replace(".", "-"), 1.0


def ticker_file(ticker: str) -> str:
    return re.sub(r"[^A-Z0-9.]", "_", ticker.upper()) + ".csv"


# --------------------------------------------------------------------------- opciones
def calculate_strike(spot_price: float, otm_pct: float = 0.95, option_type: str = "CALL") -> float:
    """Strike OTM segun PROTOCOLO §2: CALL 5% arriba del spot, PUT 5% abajo."""
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
    """IV estimada = vol realizada 20d; sin historia suficiente, valor de respaldo de
    config.json (0.30 si el ticker no esta). Acotada a [0.10, 1.50]."""
    rv = realized_vol(closes)
    if rv is None:
        rv = float(cfg.get("iv_fallback", {}).get(ticker, 0.30))
    return min(max(rv, 0.10), 1.50)


# --------------------------------------------------------------------------- mercado
def get_daily_history(ticker: str, period: str = "3mo") -> list[dict]:
    """Velas diarias de yfinance: [{date, open, high, low, close}]. La ultima puede ser la
    del dia en curso (close = precio actual). Lanza excepcion si falla."""
    import yfinance as yf  # import tardio: los scripts de calculo no lo necesitan

    ysym, factor = yahoo_symbol(ticker)
    df = yf.Ticker(ysym).history(period=period, interval="1d", auto_adjust=False)
    out = []
    for idx, row in df.iterrows():
        vals = [to_float(row.get(k)) for k in ("Open", "High", "Low", "Close")]
        if any(v is None for v in vals[1:]):
            continue
        o, h, l, c = (v * factor if v is not None else None for v in vals)
        out.append({"date": idx.date(), "open": o if o is not None else c, "high": h, "low": l, "close": c})
    if not out:
        raise RuntimeError(f"yfinance devolvio 0 velas para {ticker}")
    return out


# --------------------------------------------------------------------------- correo
def send_email(subject: str, text: str, html: str | None = None) -> bool:
    """Envia un correo (texto + HTML opcional). Requiere SMTP_HOST, SMTP_USER, SMTP_PASS y
    ALERT_EMAIL_TO; sin ellos no hace nada. Nunca registra la direccion en el log."""
    host, user, pwd, to = (os.getenv(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "ALERT_EMAIL_TO"))
    if not all((host, user, pwd, to)):
        log_event("Correo no enviado: faltan credenciales SMTP", "WARNING")
        return False
    try:
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = subject, user, to
        msg.set_content(text)
        if html:
            msg.add_alternative(html, subtype="html")
        with smtplib.SMTP_SSL(host, int(os.getenv("SMTP_PORT", "465")), timeout=30) as s:
            s.login(user, pwd)
            s.send_message(msg)
        log_event(f"Correo enviado: {subject}")
        return True
    except Exception as e:  # una alerta fallida nunca debe tumbar el pipeline
        log_event(f"Correo fallo ({subject}): {type(e).__name__}", "WARNING")
        return False


def send_email_alert(subject: str, body: str) -> bool:
    """Compatibilidad con la version 1."""
    return send_email(subject, body)
