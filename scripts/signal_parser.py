"""Parser de señales de opciones del canal de Vicente Luz — version 2.

Funciones puras (sin red ni archivos) para poder probarlas: tests/test_parser.py.

Por que existe la version 2: la version 1 pasaba el texto a MAYUSCULAS antes de buscar
tickers, asi que cualquier palabra de 2-5 letras ("the", "for", "at", "dec") se volvia un
ticker. Ninguna regla de esta version depende de una lista de palabras excluidas:

1. El texto se lee tal como fue escrito. Un ticker es un simbolo escrito en MAYUSCULAS en el
   mensaje (o con $: $NVDA, $nvda) que existe en la lista de simbolos que cotizan en EE. UU.
   (data/ref/us_symbols.csv). Los de una letra (F, T, C) solo valen con $ o pegados al
   contrato ("F Nov 14 calls").
   v3: el canal tambien escribe tickers en minusculas ("Fcx jan 50 calls"). Una palabra en
   minusculas o con solo la inicial en mayuscula cuenta como ticker SOLO si: esta pegada al
   contrato, tiene 3 letras o mas, esta en la lista, la linea no trae otro ticker en
   mayusculas, y (paso posterior, scripts/1_parser_signals.py) el strike publicado es
   coherente con el precio real de ese dia. Asi "Grab Jan 50 calls" no se lee como GRAB.
2. Estructura de señal, en la misma linea: direccion (call/put, calls/puts o 200C/200P) y al
   menos uno de: strike, vencimiento (mes o fecha) o prima (@ 2.19, at 2.19, for 1.20 per).
3. El ticker de la señal es el simbolo mas cercano ANTES del contrato (o justo despues:
   "calls on $NVDA"). En "Buy to open the KWEB Dec 40 calls" gana KWEB, no OPEN; en
   "TTWO DEC 260 CALLS AT 5.40", DEC es el vencimiento y AT marca la prima.
4. Una linea de cierre (sold, STC, to close, took profits, vendi...) no abre señal.
   v4: spreads. Si el mensaje compra y vende el mismo tipo de opcion del mismo ticker
   ("BUY XSP $727 Call / SELL XSP $728 Call", "Buy to Open 28920 NDX Put / Sell to Open 28930
   NDX Put"), es UNA señal con la direccion del spread: comprar el strike menor y vender el
   mayor es alcista (CALL); al reves, bajista (PUT). Asi un spread de credito con puts se
   registra como CALL. La pierna vendida queda "pierna_de_spread"; una venta sola de opciones,
   "venta_de_opciones" (el protocolo solo compra).
   v4: XSP y SPX (mismo subyacente) en el mismo mensaje cuentan una sola vez.
5. Mismo ticker con CALL y PUT en el mismo mensaje: direccion ambigua, se descarta.
6. Reposteos: cuenta la primera aparicion de un contrato; las siguientes compatibles dentro
   de `repost_window_days` dias quedan "descartada / repeticion" con referencia a la primera.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime

from common_utils import yahoo_symbol

PARSER_VERSION = 4

MONTHS = {
    # ingles
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4,
    "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9,
    "sept": 9, "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12,
    "december": 12,
    # español
    "ene": 1, "enero": 1, "febrero": 2, "marzo": 3, "abr": 4, "abril": 4, "mayo": 5,
    "junio": 6, "julio": 7, "ago": 8, "agosto": 8, "septiembre": 9, "setiembre": 9,
    "octubre": 10, "noviembre": 11, "dic": 12, "diciembre": 12,
}
# Meses que en minusculas son palabras comunes ("you may", "march on", "mar"): solo cuentan
# con mayuscula inicial o seguidos de un numero.
_MONTH_NEEDS_CAP = {"may", "march", "mar"}

_DIR_WORDS = {"call": "CALL", "calls": "CALL", "put": "PUT", "puts": "PUT"}
_PREMIUM_MARKS = {"@", "at", "for", "prima", "premium", "paid", "pay", "price", "precio", "costo"}
_PREMIUM_FILLERS = {"about", "around", "approx", "approximately", "aprox", "only", "just", "under",
                    "below", "near", "the", "more", "than", "no", "up", "to", "de", "unos", "alrededor",
                    "of", "a", "max", "maximo", "máximo", "limit", "límite", "limite"}
_EXIT_RE = re.compile(
    r"\b(sold|selling|sell\s+to\s+close|to\s+close|stc|closed|closing\s+out|trim(?:med|ming)?|"
    r"took\s+(?:some\s+)?profits?|taking\s+(?:some\s+)?profits?|profits?\s+taken|exit(?:ed|ing)?|"
    r"stopped\s+out|vend[ií](?:mos)?|vendid[oa]s?|cerr[ée](?:mos)?|cerrad[oa]s?)\b", re.I)
# Pierna vendida al abrir (spread o venta de opciones): "SELL XSP ... Call", "Sell to Open".
_SELL_RE = re.compile(r"\b(sell|sto|short)\b", re.I)

_TOKEN_RE = re.compile(r"""
    (?P<time>(?<![\d.])\d{1,2}:\d{2}(?![\d]))
  | (?P<pct>[+-]?\d+(?:[.,]\d+)?\s?%)
  | (?P<iso>(?<!\d)\d{4}-\d{2}-\d{2}(?!\d))
  | (?P<date>(?<![\d/.])\d{1,2}/\d{1,2}(?:/\d{2,4})?(?![\d/]))
  | (?P<ord>(?<![\d.])\d{1,2}(?i:st|nd|rd|th)(?![A-Za-z]))
  | (?P<num>\$?\d+(?:,\d{3})*(?:[.,]\d+)?(?:[cCpP](?![A-Za-z]))?)
  | (?P<word>\$?[A-Za-zÁÉÍÓÚÑÜáéíóúñü]+(?:\.[A-Z](?![A-Za-z]))?(?:['’][A-Za-z]+)?)
  | (?P<at>@)
""", re.X)


@dataclass
class Tok:
    kind: str          # time | pct | iso | date | num | word | at
    text: str
    value: float | None = None
    suffix_dir: str | None = None   # "200C" -> CALL

    @property
    def base(self) -> str:
        """Palabra sin $ ni posesivo ("NVDA's" -> "NVDA")."""
        return re.split(r"['’]", self.text.lstrip("$"))[0]

    @property
    def lower(self) -> str:
        return self.base.lower()


def _num_value(s: str) -> float | None:
    s = s.lstrip("$")
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", s):
        s = s.replace(",", "")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def tokenize(line: str) -> list[Tok]:
    out = []
    for m in _TOKEN_RE.finditer(line):
        kind = m.lastgroup
        text = m.group(kind)
        if kind == "ord":   # "Nov 20th": dia del vencimiento, no el ticker TH
            out.append(Tok("num", text, float(re.match(r"\d+", text).group())))
        elif kind == "num":
            suffix = None
            if text[-1] in "cCpP":
                suffix = "CALL" if text[-1] in "cC" else "PUT"
                text_num = text[:-1]
            else:
                text_num = text
            out.append(Tok("num", text, _num_value(text_num), suffix))
        else:
            out.append(Tok(kind, text))
    return out


def _dir_of(tok: Tok) -> str | None:
    if tok.kind == "num":
        return tok.suffix_dir
    if tok.kind == "word":
        return _DIR_WORDS.get(tok.lower)
    return None


def _month_of(toks: list[Tok], i: int) -> int | None:
    tok = toks[i]
    if tok.kind != "word" or tok.text.startswith("$"):
        return None
    m = MONTHS.get(tok.lower)
    if m is None:
        return None
    if tok.lower in _MONTH_NEEDS_CAP and tok.base.islower():
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if not (nxt and nxt.kind == "num" and nxt.suffix_dir is None):
            return None
    return m


def _is_plain_num(tok: Tok) -> bool:
    return tok.kind == "num" and tok.suffix_dir is None and tok.value is not None


def _ticker_of(tok: Tok, symbols: set[str], adjacent: bool, allow_lower: bool = False) -> str | None:
    """Ticker si el token esta escrito como ticker y existe en la lista de EE. UU.
    Con allow_lower, acepta minusculas (3+ letras, pegado al contrato): ver regla 1."""
    if tok.kind != "word":
        return None
    base = tok.base
    cashtag = tok.text.startswith("$")
    sym = base.upper()
    if not cashtag:
        letters = sym.replace(".", "")
        if not (letters.isalpha() and letters.isascii()):
            return None
        if base != sym and not (allow_lower and adjacent and len(letters) >= 3):
            return None   # escrito en minusculas o mixto: es una palabra, no un ticker
    if not re.fullmatch(r"[A-Z]{1,5}(?:\.[A-Z])?", sym) or sym not in symbols:
        return None
    if len(sym) == 1 and not (cashtag or adjacent):
        return None
    return sym


@dataclass
class Candidate:
    ticker: str
    direction: str
    strike: float | None
    expiry: str
    premium: float | None
    line_no: int
    estado: str = "valida"
    escrito: str = "mayusculas"   # mayusculas | cashtag | minusculas
    motivo: str = ""
    accion: str = "compra"        # compra | venta (pierna vendida al abrir)
    spread: str = ""              # "PUT 28920/28930" si la señal es un spread


def _resolve_expiry(month: int, day: int | None, year: int | None, msg_date: date) -> str:
    if year is None:
        year = msg_date.year
        if month < msg_date.month or (month == msg_date.month and day is not None and day < msg_date.day):
            year += 1
    return f"{year:04d}-{month:02d}-{day:02d}" if day else f"{year:04d}-{month:02d}"


def _is_friday(month: int, day: int, msg_date: date) -> bool:
    """¿Mes + dia es un viernes (dia normal de vencimiento de opciones)?"""
    exp = _resolve_expiry(month, day, None, msg_date)
    try:
        return date.fromisoformat(exp).weekday() == 4
    except ValueError:
        return False


def _is_int(tok: Tok, lo: int, hi: int) -> bool:
    return _is_plain_num(tok) and tok.value.is_integer() and lo <= tok.value <= hi


def _expiry_near(toks: list[Tok], i: int, strike_idx: int | None,
                 msg_date: date) -> tuple[str, int | None, set[int]]:
    """Vencimiento mas cercano al token de direccion `i` (ventana -5..+4 tokens).
    Devuelve (vencimiento, indice del mes o fecha, indices que forman el vencimiento)."""
    best = None
    for j in range(max(0, i - 5), min(len(toks), i + 5)):
        tok = toks[j]
        exp = None
        used = {j}
        if tok.kind == "iso":
            y, mo, d = (int(x) for x in tok.text.split("-"))
            if 1 <= mo <= 12 and 1 <= d <= 31:
                exp = f"{y:04d}-{mo:02d}-{d:02d}"
        elif tok.kind == "date":
            parts = [int(x) for x in tok.text.split("/")]
            mo, d = parts[0], parts[1]
            y = parts[2] if len(parts) == 3 else None
            if y is not None and y < 100:
                y += 2000
            if 1 <= mo <= 12 and 1 <= d <= 31:
                exp = _resolve_expiry(mo, d, y, msg_date)
        else:
            mo = _month_of(toks, j)
            if mo is not None:
                day = year = None
                if j > 0 and j - 1 != strike_idx and _is_int(toks[j - 1], 1, 31):   # "23 OCT", "9 NOV 2026"
                    day = int(toks[j - 1].value)
                    used.add(j - 1)
                k = j + 1
                if k < len(toks) and k != strike_idx and _is_int(toks[k], 1, 2031):
                    v = int(toks[k].value)
                    if 2025 <= v <= 2031:
                        year = v
                    elif day is not None and 25 <= v <= 31 and len(toks[k].text.lstrip("$")) == 2:
                        year = 2000 + v   # "23 OCT 26": dia, mes, año
                    elif day is None and 1 <= v <= 31:
                        day = v
                    if year is not None or day == v:
                        used.add(k)
                        k += 1
                if year is None and k < len(toks) and k != strike_idx and _is_int(toks[k], 2025, 2031):
                    year = int(toks[k].value)
                    used.add(k)
                exp = _resolve_expiry(mo, day, year, msg_date)
        if exp and (best is None or abs(j - i) < abs(best[1] - i)):
            best = (exp, j, used)
    return best if best else ("", None, set())


def _premium_in(toks: list[Tok], from_idx: int = 0) -> float | None:
    for j in range(from_idx, len(toks)):
        tok = toks[j]
        is_mark = tok.kind == "at" or (tok.kind == "word" and tok.lower in _PREMIUM_MARKS)
        if not is_mark:
            continue
        k = j + 1
        skipped = 0
        while k < len(toks) and toks[k].kind == "word" and toks[k].lower in _PREMIUM_FILLERS and skipped < 3:
            k, skipped = k + 1, skipped + 1
        if k < len(toks) and _is_plain_num(toks[k]):
            nxt = toks[k + 1] if k + 1 < len(toks) else None
            if nxt is not None and _dir_of(nxt):   # "for 200 calls": es strike, no prima
                continue
            v = toks[k].value
            if 0.01 <= v <= 500:
                return v
    return None


def _line_tickers(toks: list[Tok], symbols: set[str]) -> list[str]:
    return [t for t in (_ticker_of(tok, symbols, adjacent=False) for tok in toks) if t]


def parse_message(text: str, symbols: set[str], msg_date: date | None = None) -> list[Candidate]:
    """Candidatas de un mensaje (validas y descartadas) segun las reglas del modulo."""
    if not text:
        return []
    msg_date = msg_date or date.today()
    lines = text.splitlines()
    tokd = [tokenize(ln) for ln in lines]
    raw: list[Candidate] = []
    for ln, toks in enumerate(tokd):
        exit_line = bool(_EXIT_RE.search(lines[ln]))
        sell_line = not exit_line and bool(_SELL_RE.search(lines[ln]))
        line_ticker = None   # ticker del contrato anterior en la misma linea ("NVDA 200C, 210C")
        for i, tok in enumerate(toks):
            direction = _dir_of(tok)
            if not direction:
                continue
            strike_idx = None
            strike = None
            if tok.kind == "num":
                strike, strike_idx = tok.value, i
            elif i > 0 and _is_plain_num(toks[i - 1]):
                strike, strike_idx = toks[i - 1].value, i - 1
                month = _month_of(toks, i - 2) if i > 1 else None
                v = toks[i - 1].value
                if month is not None and v.is_integer():
                    # "Nov 20 calls": el numero pegado al mes puede ser el dia o el año
                    alt = i - 3 if i > 2 and _is_plain_num(toks[i - 3]) else None   # "INTC 40 Nov 20 calls"
                    if 2025 <= v <= 2031:
                        strike, strike_idx = (toks[alt].value, alt) if alt is not None else (None, None)
                    elif 1 <= v <= 31 and (alt is not None or _is_friday(month, int(v), msg_date)):
                        strike, strike_idx = (toks[alt].value, alt) if alt is not None else (None, None)
            elif i > 1 and _is_plain_num(toks[i - 2]) and toks[i - 1].lower == "strike":
                strike, strike_idx = toks[i - 2].value, i - 2
            elif i > 1 and _is_plain_num(toks[i - 2]) and _month_of(toks, i - 1) is None \
                    and _ticker_of(toks[i - 1], symbols, adjacent=True):
                strike, strike_idx = toks[i - 2].value, i - 2   # "28920 NDX Put"
            expiry, exp_idx, exp_used = _expiry_near(toks, i, strike_idx, msg_date)
            used = exp_used | ({strike_idx} if strike_idx is not None else set())
            spec_start = min(used | {i})
            ticker = None
            for k in range(spec_start + 1, i):   # dentro del contrato: "October 16 CVX $215 calls"
                if k in used or _month_of(toks, k) is not None:
                    continue
                ticker = _ticker_of(toks[k], symbols, adjacent=(k == i - 1 or k + 1 in used))
                if ticker:
                    break
            for k in range(spec_start - 1, max(-1, spec_start - 7), -1):
                if ticker:
                    break
                if _dir_of(toks[k]):
                    ticker = line_ticker   # "NVDA Dec 200 calls and Dec 180 puts"
                    break
                if k in used or _month_of(toks, k) is not None:
                    continue
                ticker = _ticker_of(toks[k], symbols, adjacent=(k == spec_start - 1))
                if ticker:
                    break
            escrito = "mayusculas"
            k = spec_start - 1   # v3: ticker en minusculas solo pegado al contrato ("Fcx jan 50 calls")
            if not ticker and strike is not None and k >= 0 and k not in used and _month_of(toks, k) is None \
                    and not _dir_of(toks[k]):
                ticker = _ticker_of(toks[k], symbols, adjacent=True, allow_lower=True)
                escrito = "minusculas" if ticker else escrito
            if not ticker:
                for k in range(i + 1, min(len(toks), i + 5)):
                    if _dir_of(toks[k]):
                        break
                    if k in used or _month_of(toks, k) is not None:
                        continue
                    ticker = _ticker_of(toks[k], symbols, adjacent=(k == i + 1))
                    if ticker:
                        break
            if not ticker:   # mensaje partido en lineas: "$NVDA" arriba, "Dec 200 calls" abajo
                prev = []
                for pl in range(ln - 1, max(-1, ln - 3), -1):
                    if any(_dir_of(t) for t in tokd[pl]):
                        break
                    prev += _line_tickers(tokd[pl], symbols)
                if len(set(prev)) == 1:
                    ticker = prev[0]
            if not ticker:
                continue
            line_ticker = ticker
            premium = _premium_in(toks, spec_start)
            if premium is None:
                premium = _premium_in(toks, 0)
            if escrito == "mayusculas" and any(t.kind == "word" and t.text.startswith("$") and
                                               t.base.upper() == ticker for t in toks):
                escrito = "cashtag"
            cand = Candidate(ticker, direction, strike, expiry, premium, ln + 1, escrito=escrito,
                             accion="venta" if sell_line else "compra")
            if exit_line:
                cand.estado, cand.motivo = "descartada", "cierre_o_venta"
            elif strike is None and not expiry and premium is None:
                cand.estado, cand.motivo = "descartada", "sin_estructura"
            raw.append(cand)
    _apply_spreads(raw)
    _same_underlying(raw)
    # una candidata por (ticker, direccion): gana la valida y, entre iguales, la primera
    best: dict[tuple[str, str], Candidate] = {}
    for c in raw:
        key = (c.ticker, c.direction)
        cur = best.get(key)
        if cur is None or (cur.estado != "valida" and c.estado == "valida"):
            best[key] = c
    out = list(best.values())
    valid_dirs: dict[str, set[str]] = {}
    for c in out:
        if c.estado == "valida":
            valid_dirs.setdefault(c.ticker, set()).add(c.direction)
    for c in out:
        if c.estado == "valida" and len(valid_dirs[c.ticker]) > 1:
            c.estado, c.motivo = "descartada", "direccion_ambigua"
    return out


def _apply_spreads(raw: list[Candidate]) -> None:
    """Piernas compradas y vendidas del mismo ticker y tipo en un mensaje = un spread."""
    groups: dict[tuple[str, str], list[Candidate]] = defaultdict(list)
    for c in raw:
        if c.motivo != "cierre_o_venta":
            groups[(c.ticker, c.direction)].append(c)
    for (_t, otype), cs in groups.items():
        buys = [c for c in cs if c.accion == "compra"]
        sells = [c for c in cs if c.accion == "venta"]
        if sells and not buys:
            for c in sells:
                c.estado, c.motivo = "descartada", "venta_de_opciones"
        elif sells:
            b = next((c for c in buys if c.strike is not None), buys[0])
            v = next((c for c in sells if c.strike is not None), sells[0])
            view = otype
            if b.strike is not None and v.strike is not None and b.strike != v.strike:
                view = "CALL" if b.strike < v.strike else "PUT"   # compra menor/vende mayor: alcista
                spread = f"{otype} {b.strike:g}/{v.strike:g}"
            else:
                spread = otype
            for c in buys:
                c.direction, c.spread = view, spread
            for c in sells:
                c.estado, c.motivo = "descartada", "pierna_de_spread"


def _same_underlying(raw: list[Candidate]) -> None:
    """XSP y SPX (mismo indice) en el mismo mensaje y misma direccion: una sola señal."""
    first: dict[tuple[str, str], str] = {}
    for c in raw:
        if c.estado != "valida":
            continue
        key = (yahoo_symbol(c.ticker)[0], c.direction)
        if key in first and first[key] != c.ticker:
            c.estado, c.motivo = "descartada", "mismo_subyacente"
        else:
            first.setdefault(key, c.ticker)


def message_motivo(text: str, cands: list[Candidate]) -> str:
    """Motivo a nivel de mensaje cuando no deja ninguna señal valida."""
    if any(c.estado == "valida" for c in cands):
        return ""
    if not (text or "").strip():
        return "sin_texto"
    if cands:
        return "solo_descartadas"
    if any(_dir_of(t) for ln in text.splitlines() for t in tokenize(ln)):
        return "sin_ticker_valido"
    return "sin_direccion"


def text_sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- reposteos
def _f(x) -> float | None:
    try:
        return float(x) if x not in (None, "") else None
    except (TypeError, ValueError):
        return None


def same_contract(a: dict, b: dict) -> bool:
    """¿b repite el contrato de a? Mismo ticker y direccion, sin datos que se contradigan."""
    if a["ticker"] != b["ticker"] or a["signal_type"] != b["signal_type"]:
        return False
    sa, sb = _f(a.get("strike_vl")), _f(b.get("strike_vl"))
    ea, eb = a.get("expiry_vl") or "", b.get("expiry_vl") or ""
    if sa is not None and sb is not None and abs(sa - sb) > 1e-6:
        return False
    if ea and eb and (ea[:7] != eb[:7] or (len(ea) == 10 and len(eb) == 10 and ea != eb)):
        return False
    a_spec, b_spec = sa is not None or ea, sb is not None or eb
    if not a_spec and not b_spec:   # ninguno trae contrato: comparar la prima
        pa, pb = _f(a.get("price_recommended")), _f(b.get("price_recommended"))
        return pa is None or pb is None or abs(pa - pb) < 1e-6
    return True


def mark_reposts(rows: list[dict], history: list[dict], window_days: float) -> list[dict]:
    """Marca como repeticion las filas validas que repiten un contrato anterior.

    `rows` (nuevas) e `history` (ya guardadas) son dicts con las columnas de signals.csv.
    Se recorren en orden cronologico; solo se modifican las filas de `rows`."""
    originals = [h for h in history if h.get("estado") == "valida"]
    for r in sorted(rows, key=lambda x: (x["timestamp"], int(x["message_id"]), x.get("linea") or "")):
        if r.get("estado") != "valida":
            continue
        ts = datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S")
        for o in originals:
            if o["message_id"] == r["message_id"]:
                continue
            age = (ts - datetime.strptime(o["timestamp"], "%Y-%m-%d %H:%M:%S")).total_seconds() / 86400
            if 0 <= age <= window_days and same_contract(o, r):
                r["estado"], r["motivo"], r["repite_a"] = "descartada", "repeticion", o["signal_id"]
                break
        else:
            originals.append(r)
    return rows


# --------------------------------------------------------------------------- auditoria v1
def audit_v1_row(old: dict, text: str | None, cands: list[dict], symbols: set[str]) -> tuple[str, str, str]:
    """(estado_v2, motivo_v2, signal_id_v2) para una fila de signals.csv version 1."""
    tok, typ = old["ticker"], old["signal_type"]
    if text is None:
        return "descartada", "mensaje_no_disponible", ""
    same = [c for c in cands if c["ticker"] == tok]
    for c in same:
        if c["signal_type"] == typ and c["estado"] == "valida":
            return "valida", "", c["signal_id"]
    if any(c["estado"] == "valida" for c in same):
        return "descartada", "direccion_distinta", ""   # p. ej. spread de credito con puts = CALL
    for c in same:
        if c["signal_type"] == typ:
            return c["estado"], c["motivo"], ""
    if same:
        return "descartada", "direccion_distinta", ""
    if tok.lower() in MONTHS:
        return "descartada", "es_mes", ""
    esc = re.escape(tok)
    if not re.search(rf"(?<![A-Za-z0-9]){esc}(?![A-Za-z0-9])", text, re.I):
        return "descartada", "no_aparece_en_texto", ""
    as_ticker = re.search(rf"(?<![A-Za-z0-9]){esc}(?![A-Za-z0-9])", text) or \
        re.search(rf"\${esc}(?![A-Za-z0-9])", text, re.I)
    if not as_ticker:
        return "descartada", "palabra_en_minusculas", ""
    if tok not in symbols:
        return "descartada", "no_cotiza_en_eeuu", ""
    if any(c["estado"] == "valida" for c in cands):
        return "descartada", "no_es_el_ticker_de_la_senal", ""
    return "descartada", "sin_estructura_de_senal", ""
