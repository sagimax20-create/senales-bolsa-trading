"""Pruebas del parser v2 (scripts/signal_parser.py) y de la lectura de mensajes (script 1).

Los mensajes son RECONSTRUIDOS: las palabras salen de mensajes reales del canal (se cita el
message_id y lo que extrajo la version 1), pero los numeros (strike, prima, dia) son
ilustrativos porque la version 1 no los guardo. Los mensajes reales, cuando existan, van en
tests/fixtures/mensajes_reales.json y se prueban en RealMessagesTest.
"""
import json
import unittest
from datetime import date, datetime, timezone

from _paths import ROOT, load_script

from common_utils import load_symbols  # noqa: E402
from signal_parser import audit_v1_row, mark_reposts, message_motivo, parse_message  # noqa: E402

SYMBOLS = load_symbols()
WORDS_THAT_ARE_NOT_SIGNALS = {"THE", "FOR", "AT", "PER", "BUY", "TRADE", "BELOW", "AND", "TO", "MY", "PLAN",
                              "DEC", "NOV", "JAN", "MARCH", "MAR", "ENERO", "PT", "OPEX", "ALSO", "WON", "PAY"}


def parse(text, d=date(2026, 11, 2)):
    return [(c.ticker, c.direction, c.estado, c.motivo) for c in parse_message(text, SYMBOLS, d)]


def valid(text, d=date(2026, 11, 2)):
    return [(c.ticker, c.direction) for c in parse_message(text, SYMBOLS, d) if c.estado == "valida"]


# (message_id real de referencia, lo que extrajo la v1, texto reconstruido, fecha, señales validas esperadas)
RECONSTRUIDOS = [
    (72880, "ALSO THE OSCR FOR PER WON PAY MORE THAN",
     "Also the OSCR 1/16 20 calls for 1.10 per, won't pay more than 1.25", date(2026, 10, 5), [("OSCR", "CALL")]),
    (72881, "FCX JAN AT", "FCX Jan 50 calls at 2.35", date(2026, 10, 5), [("FCX", "CALL")]),
    (72889, "NVDA DEC", "NVDA Dec 200 calls", date(2026, 10, 5), [("NVDA", "CALL")]),
    (72890, "BUY TO OPEN THE KWEB FOR ABOUT AND UP",
     "Buy to open the KWEB December 40 calls for about 1.20 and up", date(2026, 10, 5), [("KWEB", "CALL")]),
    (72952, "MY TRADE PLAN CVX STOP TSLA BELOW",
     "My trade plan: CVX calls, stop 150.\nTSLA calls below 400.", date(2026, 10, 7), []),
    (73164, "BUY WMT NOV @2.08", "Buy WMT Nov 105 calls @2.08", date(2026, 10, 14), [("WMT", "CALL")]),
    (73323, "NVDA BUY @2.19", "NVDA 12/19 190 calls BUY @2.19", date(2026, 10, 19), [("NVDA", "CALL")]),
    (73340, "LOTTA CRWV GOING TO TAKE THE CAUSE THEIR CHEAP BUY @0.53",
     "Lotta CRWV calls going to take the cause their cheap, buy @0.53", date(2026, 10, 19), [("CRWV", "CALL")]),
    (73621, "NOV @0.69 (la v1 perdio el ticker de 1 letra)", "F Nov 14 calls @0.69", date(2026, 10, 27),
     [("F", "CALL")]),
    (72910, "THE FROM ARE BELOW META PRINT IS HUGE ... PT ... AI SEMI",
     "The calls from yesterday are up, below the META print is huge, broke out of high and tight flag "
     "with PT 850. Also to be in AI semi names.", date(2026, 10, 6), []),
    (73848, "NVDA ENERO AT", "NVDA enero 180 calls at 9.25", date(2026, 11, 9), [("NVDA", "CALL")]),
    (74031, "NVDL MARCH AT", "NVDL March 80 calls at 3.10", date(2026, 11, 16), [("NVDL", "CALL")]),
    (73870, "THE MSFT FOR", "The MSFT Jan 560 calls for 18.00 per contract", date(2026, 11, 9), [("MSFT", "CALL")]),
    (74139, "MY TRADE PLAN INTC STOP BELOW", "My trade plan: INTC calls, stop below 50", date(2026, 11, 19), []),
    (74000, "WHAT LIKE RIGHT NOW CASH LOTS OF IS THE BEST TRADE IN MY AS WILL TO BE",
     "What I like right now is cash, lots of it. Is the best trade in my opinion as NVDA puts will "
     "have to wait to be sure.", date(2026, 11, 13), []),
    (74170, "WHAT LIKE RIGHT NOW ... OPEX ... XRT XLY ... LISA COOK (60 filas)",
     "What I like right now is cash and lots of it until we have some sign.\nToday is OPEX day, tops can "
     "happen.\nStill think even a bad Black Friday and Cyber Monday XRT and XLY could pop next week.\n"
     "Puts are working but I need to see one green day first. Lisa Cook headlines too.",
     date(2026, 11, 20), []),
    (73808, "HOUR PLAN TRADE ... SELL ... DEBIT ... GTC ... SPX (72 filas)",
     "If you need help go here to our site, it is built to walk you through.\nSell the SPX Nov 6000 call "
     "to close when the debit spread hits our limit, GTC good till cancelled.", date(2026, 11, 6), []),
]


class ReconstructedMessagesTest(unittest.TestCase):
    def test_expected_signals(self):
        for mid, v1, text, d, expected in RECONSTRUIDOS:
            with self.subTest(message_id=mid, v1=v1):
                self.assertEqual(valid(text, d), expected)

    def test_words_and_months_are_never_tickers(self):
        """THE, FOR, AT, PER y los meses (DEC, NOV, JAN, MARCH, ENERO) no salen como ticker."""
        for mid, _v1, text, d, _exp in RECONSTRUIDOS:
            with self.subTest(message_id=mid):
                tickers = {t for t, *_ in parse(text, d)}
                self.assertFalse(tickers & WORDS_THAT_ARE_NOT_SIGNALS, tickers)

    def test_all_caps_message_with_words_that_are_real_tickers(self):
        """FOR, AT, OPEN y DEC cotizan en EE. UU., pero aqui no son el ticker de la señal."""
        for w in ("FOR", "AT", "OPEN", "DEC"):
            self.assertIn(w, SYMBOLS)
        self.assertEqual(valid("BUY TO OPEN THE KWEB DEC 40 CALLS FOR ABOUT 1.20"), [("KWEB", "CALL")])
        self.assertEqual(valid("TTWO DEC 260 CALLS AT 5.40"), [("TTWO", "CALL")])

    def test_real_tickers_that_are_english_words(self):
        self.assertEqual(valid("OPEN Jan 10 calls at 0.85"), [("OPEN", "CALL")])
        self.assertEqual(valid("NOW Dec 900 calls @ 20"), [("NOW", "CALL")])
        self.assertEqual(valid("ON Dec 70 calls at 2.10"), [("ON", "CALL")])

    def test_lowercase_words_are_not_tickers(self):
        self.assertEqual(parse("the calls for nvda look good at the open"), [])
        self.assertEqual(valid("$nvda Dec 200 calls"), [("NVDA", "CALL")])  # con $ si vale

    def test_strike_suffix_notation(self):
        """La v1 ignoraba "240C" porque no traia la palabra call."""
        cs = parse_message("$IWM 12/19 240C @ 1.85", SYMBOLS, date(2026, 11, 2))
        self.assertEqual([(c.ticker, c.direction, c.strike, c.expiry, c.premium) for c in cs],
                         [("IWM", "CALL", 240.0, "2026-12-19", 1.85)])
        self.assertEqual(valid("SPY 12/19 640p at 3.20"), [("SPY", "PUT")])

    def test_fields_extracted(self):
        c = parse_message("Also the OSCR 1/16 20 calls for 1.10 per, won't pay more than 1.25", SYMBOLS,
                          date(2026, 10, 5))[0]
        self.assertEqual((c.strike, c.expiry, c.premium), (20.0, "2027-01-16", 1.1))
        c = parse_message("FCX Jan 50 calls at 2.35", SYMBOLS, date(2026, 10, 5))[0]
        self.assertEqual((c.strike, c.expiry, c.premium), (50.0, "2027-01", 2.35))
        c = parse_message("NVDA Dec 19 2026 200 calls", SYMBOLS, date(2026, 11, 2))[0]
        self.assertEqual((c.strike, c.expiry), (200.0, "2026-12-19"))

    def test_without_structure_is_discarded(self):
        self.assertEqual(parse("COIN calls are working"), [("COIN", "CALL", "descartada", "sin_estructura")])

    def test_close_or_sell_lines_do_not_open_signals(self):
        self.assertEqual(parse("Sold the NVDA Dec 200 calls at 4.20, +90%"),
                         [("NVDA", "CALL", "descartada", "cierre_o_venta")])
        self.assertEqual(valid("Took profits on TSLA Dec 450 calls @ 9.10"), [])

    def test_call_and_put_same_ticker_is_ambiguous(self):
        self.assertEqual({m for *_, m in parse("NVDA Dec 200 calls and Dec 180 puts")}, {"direccion_ambigua"})

    def test_two_signals_in_one_line(self):
        self.assertEqual(valid("NVDA 200C and AMD 150C Dec 19"), [("NVDA", "CALL"), ("AMD", "CALL")])

    def test_single_letter_ticker_needs_cashtag_or_contract(self):
        self.assertEqual(valid("$F Jan 14c @ 0.50"), [("F", "CALL")])
        self.assertEqual(parse("A huge day for calls"), [])
        self.assertEqual(parse("I bought Dec 200 calls"), [])

    def test_ticker_on_previous_line(self):
        self.assertEqual(valid("$NVDA\nDec 200 calls @ 2.19"), [("NVDA", "CALL")])

    def test_message_motivo(self):
        self.assertEqual(message_motivo("Good morning everyone", []), "sin_direccion")
        self.assertEqual(message_motivo("calls are working today", []), "sin_ticker_valido")
        self.assertEqual(message_motivo("", []), "sin_texto")


def _row(mid, ts, ticker, typ, strike="", expiry="", premium="", estado="valida"):
    return {"message_id": str(mid), "timestamp": ts, "ticker": ticker, "signal_type": typ,
            "strike_vl": strike, "expiry_vl": expiry, "price_recommended": premium, "estado": estado,
            "motivo": "", "repite_a": "", "signal_id": f"VL-{mid}-{ticker}-{typ[0]}", "linea": "1"}


class RepostTest(unittest.TestCase):
    def test_same_contract_seconds_apart(self):
        """72881 y 72882: el mismo FCX publicado dos veces en 8 segundos (la v1 abrio 2 operaciones)."""
        rows = [_row(72881, "2026-10-05 19:19:37", "FCX", "CALL", "50", "2027-01", "2.35"),
                _row(72882, "2026-10-05 19:19:45", "FCX", "CALL", "50", "2027-01")]
        mark_reposts(rows, [], 45)
        self.assertEqual([r["estado"] for r in rows], ["valida", "descartada"])
        self.assertEqual(rows[1]["repite_a"], "VL-72881-FCX-C")

    def test_reposted_on_other_days_within_window(self):
        """73323, 73330 y 73384: NVDA @2.19 tres veces en dos dias."""
        rows = [_row(73323, "2026-10-19 19:10:35", "NVDA", "CALL", "190", "2026-12-19", "2.19"),
                _row(73330, "2026-10-19 19:25:54", "NVDA", "CALL", "190", "2026-12-19", "2.19"),
                _row(73384, "2026-10-20 19:23:21", "NVDA", "CALL", "190", "2026-12-19", "2.19")]
        mark_reposts(rows, [], 45)
        self.assertEqual([r["motivo"] for r in rows], ["", "repeticion", "repeticion"])

    def test_different_strike_is_a_new_signal(self):
        rows = [_row(1, "2026-11-03 20:15:43", "NVDA", "CALL", "200", "2027-03"),
                _row(2, "2026-11-09 20:14:56", "NVDA", "CALL", "180", "2027-01")]
        mark_reposts(rows, [], 45)
        self.assertEqual([r["estado"] for r in rows], ["valida", "valida"])

    def test_outside_window_is_a_new_signal(self):
        rows = [_row(1, "2026-10-01 15:00:00", "FCX", "CALL", "50", "2027-01"),
                _row(2, "2026-11-20 15:00:00", "FCX", "CALL", "50", "2027-01")]
        mark_reposts(rows, [], 45)
        self.assertEqual([r["estado"] for r in rows], ["valida", "valida"])

    def test_history_is_respected(self):
        hist = [_row(1, "2026-11-16 20:15:51", "NVDL", "CALL", "80", "2027-03", "3.10")]
        rows = [_row(2, "2026-11-18 20:17:27", "NVDL", "CALL", "80", "2027-03", "3.40")]
        mark_reposts(rows, hist, 45)
        self.assertEqual(rows[0]["motivo"], "repeticion")


class AuditV1Test(unittest.TestCase):
    """Cada fila de la v1 queda valida o descartada con un motivo concreto."""

    V1_72880 = ["ALSO", "THE", "OSCR", "FOR", "PER", "WON", "PAY", "MORE", "THAN"]

    def test_mixed_case_message(self):
        text = "Also the OSCR 1/16 20 calls for 1.10 per, won't pay more than 1.25"
        s1 = load_script("1_parser_signals")
        cfg = {"allowed_tickers": ["NVDA"], "protocol_strike_otm": 0.95, "repost_window_days": 45}
        ts = datetime(2026, 10, 5, 19, 18, 32, tzinfo=timezone.utc)
        old = [{"message_id": "72880", "ticker": t, "signal_type": "CALL"} for t in self.V1_72880]
        _sig, _msgs, audit = s1.reprocess([(72880, ts, text)], SYMBOLS, cfg, old)
        got = {a["ticker"]: (a["estado_v2"], a["motivo_v2"]) for a in audit}
        self.assertEqual(got["OSCR"], ("valida", ""))
        for w in ("ALSO", "THE", "FOR", "PER", "WON", "PAY", "MORE", "THAN"):
            self.assertEqual(got[w], ("descartada", "palabra_en_minusculas"), w)

    def test_all_caps_message(self):
        text = "BUY TO OPEN THE KWEB DEC 40 CALLS FOR ABOUT 1.20"
        cands = [{"ticker": "KWEB", "signal_type": "CALL", "estado": "valida", "motivo": "",
                  "signal_id": "VL-1-KWEB-C"}]
        res = {t: audit_v1_row({"ticker": t, "signal_type": "CALL"}, text, cands, SYMBOLS)[:2]
               for t in ("BUY", "OPEN", "THE", "KWEB", "FOR", "DEC")}
        self.assertEqual(res["KWEB"], ("valida", ""))
        self.assertEqual(res["THE"], ("descartada", "no_cotiza_en_eeuu"))
        self.assertEqual(res["BUY"], ("descartada", "no_cotiza_en_eeuu"))
        self.assertEqual(res["OPEN"], ("descartada", "no_es_el_ticker_de_la_senal"))
        self.assertEqual(res["FOR"], ("descartada", "no_es_el_ticker_de_la_senal"))
        self.assertEqual(res["DEC"], ("descartada", "es_mes"))

    def test_deleted_message(self):
        self.assertEqual(audit_v1_row({"ticker": "NVDA", "signal_type": "CALL"}, None, [], SYMBOLS)[1],
                         "mensaje_no_disponible")


class ReadMessagesTest(unittest.TestCase):
    def test_build_rows_counts_and_reasons(self):
        s1 = load_script("1_parser_signals")
        cfg = {"allowed_tickers": ["FCX", "NVDA"], "protocol_strike_otm": 0.95, "repost_window_days": 45}
        t = lambda h, m, s: datetime(2026, 10, 5, h, m, s, tzinfo=timezone.utc)  # noqa: E731
        msgs = [(72881, t(19, 19, 37), "FCX Jan 50 calls at 2.35"),
                (72882, t(19, 19, 45), "FCX Jan 50c play, chart below"),
                (72883, t(19, 20, 0), "Good luck everyone"),
                (72884, t(19, 21, 0), "calls are flying today")]
        sig, mrows, _ = s1.build_rows(msgs, SYMBOLS, cfg, [])
        self.assertEqual([(r["ticker"], r["estado"], r["motivo"], r["en_universo_original"]) for r in sig],
                         [("FCX", "valida", "", "1"), ("FCX", "descartada", "repeticion", "1")])
        self.assertEqual([m["motivo"] for m in mrows], ["", "solo_descartadas", "sin_direccion", "sin_ticker_valido"])
        self.assertTrue(all(len(m["sha256"]) == 64 and "_text" not in m for m in mrows))


class ParserV3Test(unittest.TestCase):
    """v3: el canal escribe tickers en minusculas (FCX, NVDL, TTWO... quedaron fuera en la v2) y
    pone el dia de vencimiento pegado a la direccion ("Nov 20 calls")."""

    S1 = load_script("1_parser_signals")
    CFG = {"allowed_tickers": ["FCX", "NVDA"], "protocol_strike_otm": 0.95, "repost_window_days": 45}
    PRICES = {"FCX": 48.0, "NVDL": 75.0, "GRAB": 5.2}

    def rows(self, text, d=datetime(2026, 10, 5, 19, 0, tzinfo=timezone.utc)):
        spot = lambda t, day: self.PRICES.get(t)  # noqa: E731
        sig, _m, _p = self.S1.build_rows([(1, d, text)], SYMBOLS, self.CFG, [], spot)
        return [(r["ticker"], r["estado"], r["motivo"], r["ticker_escrito"]) for r in sig]

    def test_lowercase_ticker_next_to_contract_with_coherent_strike(self):
        self.assertEqual(self.rows("Fcx jan 50 calls at 2.35"), [("FCX", "valida", "", "minusculas")])
        self.assertEqual(self.rows("nvdl March 80 calls at 3.10"), [("NVDL", "valida", "", "minusculas")])

    def test_lowercase_word_that_is_a_symbol_is_rejected_by_price(self):
        """GRAB cotiza ~$5: un strike de 50 no es coherente, asi que "Grab" era un verbo."""
        self.assertEqual(self.rows("Grab Jan 50 calls"), [("GRAB", "descartada", "minusculas_strike_incoherente",
                                                           "minusculas")])

    def test_lowercase_without_price_or_strike(self):
        self.assertEqual(self.rows("ttwo Dec 260 calls at 5.40"),
                         [("TTWO", "descartada", "minusculas_sin_precio", "minusculas")])
        self.assertEqual(parse("dia dec calls at 2.10"), [])   # sin strike: no se intenta
        self.assertEqual(parse("A huge day for calls"), [])

    def test_two_letter_lowercase_never(self):
        self.assertEqual(parse("on dec 70 calls"), [])
        self.assertEqual(parse("at dec 200 calls"), [])

    def test_uppercase_is_unchanged(self):
        self.assertEqual(self.rows("FCX Jan 50 calls at 2.35"), [("FCX", "valida", "", "mayusculas")])
        self.assertEqual(self.rows("$fcx Jan 50 calls"), [("FCX", "valida", "", "cashtag")])

    def contract(self, text, d):
        c = parse_message(text, SYMBOLS, d)[0]
        return c.ticker, c.strike, c.expiry

    def test_friday_after_month_is_the_day_not_the_strike(self):
        d = date(2026, 10, 20)
        self.assertEqual(self.contract("INTC Nov 20 calls at 1.50", d), ("INTC", None, "2026-11-20"))
        self.assertEqual(self.contract("INTC 40 Nov 20 calls", d), ("INTC", 40.0, "2026-11-20"))
        self.assertEqual(self.contract("TQQQ Dec 18 calls", date(2026, 11, 5)), ("TQQQ", None, "2026-12-18"))

    def test_non_friday_number_stays_the_strike(self):
        self.assertEqual(self.contract("F Nov 14 calls @0.69", date(2026, 10, 27)), ("F", 14.0, "2026-11"))

    def test_year_is_not_the_strike(self):
        self.assertEqual(self.contract("VST Jan 2027 calls", date(2026, 10, 5)), ("VST", None, "2027-01"))

    def test_ordinal_day_is_not_a_ticker(self):
        """La v2 leyo "20TH" como el ticker TH."""
        self.assertEqual(self.contract("Hedge: SPY Nov 20TH 650 puts", date(2026, 10, 20)),
                         ("SPY", 650.0, "2026-11-20"))
        self.assertEqual(self.contract("TQQQ Dec 18th 60 calls", date(2026, 11, 5)), ("TQQQ", 60.0, "2026-12-18"))

    def test_audit_marks_lowercase_ticker_as_valid(self):
        ts = datetime(2026, 10, 5, 19, 19, 37, tzinfo=timezone.utc)
        old = [{"message_id": "72881", "ticker": t, "signal_type": "CALL"} for t in ("FCX", "JAN", "AT")]
        _s, _m, audit = self.S1.reprocess([(72881, ts, "Fcx jan 50 calls at 2.35")], SYMBOLS, self.CFG, old,
                                          lambda t, day: self.PRICES.get(t))
        self.assertEqual([(a["ticker"], a["estado_v2"], a["motivo_v2"]) for a in audit],
                         [("FCX", "valida", ""), ("JAN", "descartada", "es_mes"),
                          ("AT", "descartada", "palabra_en_minusculas")])


class ParserV4Test(unittest.TestCase):
    """v4: formatos que aparecieron en los mensajes reales (tests/fixtures/mensajes_reales.json)."""

    def one(self, text, d=date(2026, 10, 20)):
        return [(c.ticker, c.direction, c.strike, c.expiry, c.estado, c.motivo)
                for c in parse_message(text, SYMBOLS, d)]

    def test_day_before_month_and_two_digit_year(self):
        self.assertEqual(self.one("$NVDA buy 23 OCT $225 Call @2.19")[0][3], "2026-10-23")
        self.assertEqual(self.one("BUY XSP 9 NOV 2026 $727 Call", date(2026, 11, 6))[0][3], "2026-11-09")
        self.assertEqual(self.one("Buy to Open: 23 OCT 26 28920 NDX Put", date(2026, 10, 23))[0][2:4],
                         (28920.0, "2026-10-23"))

    def test_ticker_inside_the_contract(self):
        self.assertEqual(self.one("October 16 CVX $215 calls", date(2026, 10, 7)),
                         [("CVX", "CALL", 215.0, "2026-10-16", "valida", "")])

    def test_bull_put_credit_spread_is_a_call(self):
        """Comprar el put menor y vender el mayor es alcista: la señal es CALL, no PUT."""
        text = "Buy to Open: 23 OCT 26 28920 NDX Put\nSell to Open: 23 OCT 26 28930 NDX Put"
        got = self.one(text, date(2026, 10, 23))
        self.assertIn(("NDX", "CALL", 28920.0, "2026-10-23", "valida", ""), got)
        self.assertIn(("NDX", "PUT", 28930.0, "2026-10-23", "descartada", "pierna_de_spread"), got)
        c = [c for c in parse_message(text, SYMBOLS, date(2026, 10, 23)) if c.estado == "valida"][0]
        self.assertEqual(c.spread, "PUT 28920/28930")

    def test_bull_call_debit_spread_is_a_call_and_bear_put_debit_is_a_put(self):
        self.assertEqual(valid("BUY XSP 9 NOV 2026 $727 Call\nSELL XSP 9 NOV 2026 $728 Call", date(2026, 11, 6)),
                         [("XSP", "CALL")])
        self.assertEqual(valid("Buy SPY Dec 650 puts\nSell SPY Dec 640 puts"), [("SPY", "PUT")])
        self.assertEqual(valid("Buy SPY Dec 650 calls\nSell SPY Dec 640 calls"), [("SPY", "PUT")])

    def test_lone_sell_to_open_is_not_a_signal(self):
        self.assertEqual(parse("Sell to Open NVDA Dec 150 puts at 3.10"),
                         [("NVDA", "PUT", "descartada", "venta_de_opciones")])

    def test_xsp_and_spx_in_same_message_count_once(self):
        text = "BUY XSP 9 NOV 2026 $727 Call\nBUY SPX 9 NOV 2026 $7270 Call"
        self.assertEqual([(t, e, m) for t, _d, _s, _x, e, m in self.one(text, date(2026, 11, 6))],
                         [("XSP", "valida", ""), ("SPX", "descartada", "mismo_subyacente")])

    def test_audit_v1_put_row_of_a_bullish_spread(self):
        cands = [{"ticker": "NDX", "signal_type": "CALL", "estado": "valida", "motivo": "", "signal_id": "VL-1-NDX-C"},
                 {"ticker": "NDX", "signal_type": "PUT", "estado": "descartada", "motivo": "pierna_de_spread",
                  "signal_id": "VL-1-NDX-P"}]
        self.assertEqual(audit_v1_row({"ticker": "NDX", "signal_type": "PUT"}, "x NDX y", cands, SYMBOLS)[:2],
                         ("descartada", "direccion_distinta"))


class RealMessagesTest(unittest.TestCase):
    """Lineas de alerta reales del canal (tests/fixtures/mensajes_reales.json)."""

    def test_real_messages(self):
        cases = json.loads((ROOT / "tests" / "fixtures" / "mensajes_reales.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(cases), 10)
        for case in cases:
            with self.subTest(texto=case["texto"][:40]):
                d = date.fromisoformat(case["fecha"]) if case["fecha"] else date(2026, 11, 24)
                got = [c for c in parse_message(case["texto"], SYMBOLS, d) if c.estado == "valida"]
                exp = [e for e in case["esperado"] if e.get("estado", "valida") == "valida"]
                self.assertEqual([(c.ticker, c.direction) for c in got], [(e["ticker"], e["tipo"]) for e in exp])
                for c, e in zip(got, exp):
                    if "strike" in e:
                        self.assertEqual(c.strike, float(e["strike"]))
                    if "vencimiento" in e and case["fecha"]:
                        self.assertEqual(c.expiry, e["vencimiento"])
                    if "prima" in e:
                        self.assertEqual(c.premium, float(e["prima"]))


if __name__ == "__main__":
    unittest.main()
