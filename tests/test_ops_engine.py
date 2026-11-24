"""Pruebas del motor de operaciones en papel (scripts/ops_engine.py) con velas sinteticas."""
import unittest
from datetime import datetime, timedelta, timezone

import _paths  # noqa: F401

from common_utils import DEFAULT_CONFIG, bs_price, fmt_ts  # noqa: E402
from ops_engine import build_candidates, commission_pct, evaluate  # noqa: E402

CFG = dict(DEFAULT_CONFIG)
T0 = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)


def bars(prices, start=T0, step=timedelta(hours=1), interval="60m"):
    return [{"timestamp": fmt_ts(start + i * step), "open": o, "high": h, "low": l, "close": c,
             "volume": 0, "interval": interval} for i, (o, h, l, c) in enumerate(prices)]


def cand(ts=T0, ticker="TEST", typ="CALL"):
    return {"ts": fmt_ts(ts), "ticker": ticker, "type": typ, "channel": "VICENTE_LUZ",
            "key": f"VICENTE_LUZ|VL-1-{ticker}-{typ[0]}", "signal_id": f"VL-1-{ticker}-{typ[0]}",
            "message_id": "1", "original": "0"}


FLAT = (100, 100.2, 99.8, 100)


class EntryTest(unittest.TestCase):
    def test_entry_is_open_of_first_bar_after_signal(self):
        op = evaluate(cand(T0 + timedelta(minutes=7)), bars([FLAT, (101, 101.2, 100.8, 101)]), [], CFG,
                      T0 + timedelta(hours=3))
        self.assertEqual(op["timestamp_entry"], fmt_ts(T0 + timedelta(hours=1)))
        self.assertEqual(op["spot_entry"], 101)
        self.assertEqual(op["strike_usd"], 106.05)       # CALL: spot * 1.05
        self.assertEqual(op["iv_entry"], 0.3)            # sin historia diaria: respaldo 0.30
        self.assertEqual(op["estado"], "open")

    def test_put_strike_and_iv_from_daily(self):
        daily = [{"date": f"2026-10-{d:02d}", "close": 100 + (d % 2) * 2} for d in range(1, 31)]
        op = evaluate(cand(typ="PUT"), bars([FLAT, FLAT]), daily, CFG, T0 + timedelta(hours=3))
        self.assertEqual(op["strike_usd"], 95.0)          # PUT: spot * 0.95
        self.assertNotEqual(op["iv_entry"], 0.3)

    def test_pending_then_sin_precio(self):
        self.assertEqual(evaluate(cand(), [], [], CFG, T0 + timedelta(days=1))["estado"], "pending")
        self.assertEqual(evaluate(cand(), [], [], CFG, T0 + timedelta(days=6))["estado"], "sin_precio")


class ExitRulesTest(unittest.TestCase):
    def test_take_profit_recorded_at_exactly_50(self):
        op = evaluate(cand(), bars([FLAT, (100, 109, 100, 108)]), [], CFG, T0 + timedelta(hours=3))
        self.assertEqual((op["estado"], op["reason_exit"]), ("closed", "take_profit"))
        self.assertEqual(op["retorno_bruto_pct"], 50.0)
        self.assertEqual(op["comision_pct"], 2.5)         # 1% entrada + 1% de 1.5x
        self.assertEqual(op["retorno_neto_pct"], 47.5)

    def test_stop_loss_crossed_inside_bar_is_minus_50(self):
        op = evaluate(cand(), bars([FLAT, (100, 100.2, 94, 99)]), [], CFG, T0 + timedelta(hours=3))
        self.assertEqual((op["reason_exit"], op["retorno_bruto_pct"]), ("stop_loss", -50.0))

    def test_stop_loss_on_gap_uses_observed_open(self):
        op = evaluate(cand(), bars([FLAT, (90, 90.5, 89, 90)]), [], CFG, T0 + timedelta(hours=3))
        self.assertEqual(op["reason_exit"], "stop_loss")
        self.assertLess(op["retorno_bruto_pct"], -50.0)

    def test_both_thresholds_in_same_bar_assumes_stop_loss(self):
        op = evaluate(cand(), bars([FLAT, (100, 110, 93, 100)]), [], CFG, T0 + timedelta(hours=3))
        self.assertEqual((op["reason_exit"], op["retorno_bruto_pct"]), ("stop_loss", -50.0))

    def test_put_take_profit_when_price_falls(self):
        op = evaluate(cand(typ="PUT"), bars([FLAT, (100, 100, 91, 92)]), [], CFG, T0 + timedelta(hours=3))
        self.assertEqual(op["reason_exit"], "take_profit")

    def test_expiry_when_no_more_bars(self):
        op = evaluate(cand(), bars([FLAT, FLAT]), [], CFG, T0 + timedelta(days=50))
        self.assertEqual((op["estado"], op["reason_exit"]), ("closed", "expiry"))
        self.assertEqual(op["retorno_bruto_pct"], -100.0)  # OTM al vencer: valor intrinseco 0
        self.assertEqual(op["timestamp_exit"], op["expiry"])

    def test_open_operation_is_marked_to_market(self):
        op = evaluate(cand(), bars([FLAT, (100, 101, 99.5, 100.5)]), [], CFG, T0 + timedelta(hours=3))
        self.assertEqual(op["estado"], "open")
        self.assertNotEqual(op["retorno_neto_pct"], "")


class ShadowTest(unittest.TestCase):
    """PROTOCOLO §10 nota 2: se sigue hasta el dia 45 sin cambiar el resultado oficial."""

    def test_shadow_after_take_profit(self):
        path = [FLAT, (100, 109, 100, 108), (108, 116, 108, 115), (115, 126, 115, 125)]
        op = evaluate(cand(), bars(path), [], CFG, T0 + timedelta(days=50))
        self.assertEqual((op["reason_exit"], op["retorno_bruto_pct"]), ("take_profit", 50.0))
        self.assertEqual((op["toco_100"], op["toco_150"]), (1, 1))
        self.assertGreater(op["peak_45d_pct"], 150)
        self.assertEqual(op["sombra_completa"], 1)
        p0 = op["price_entry"]
        self.assertAlmostEqual(op["ret_sin_reglas_pct"], round(((125 - 105) / p0 - 1) * 100, 2), places=1)

    def test_shadow_incomplete_before_expiry(self):
        op = evaluate(cand(), bars([FLAT, (100, 109, 100, 108)]), [], CFG, T0 + timedelta(hours=3))
        self.assertEqual(op["sombra_completa"], 0)
        self.assertNotIn("ret_sin_reglas_pct", op)


class CandidatesTest(unittest.TestCase):
    def test_only_valid_signals_and_gravity_on_original_tickers(self):
        signals = [
            {"timestamp": "2026-10-05 19:19:37", "ticker": "FCX", "signal_type": "CALL", "channel": "VICENTE_LUZ",
             "estado": "valida", "signal_id": "VL-72881-FCX-C", "message_id": "72881"},
            {"timestamp": "2026-10-05 19:19:45", "ticker": "FCX", "signal_type": "CALL", "channel": "VICENTE_LUZ",
             "estado": "descartada", "signal_id": "VL-72882-FCX-C", "message_id": "72882"},
            {"timestamp": "2026-10-06 19:00:00", "ticker": "TTWO", "signal_type": "CALL", "channel": "VICENTE_LUZ",
             "estado": "valida", "signal_id": "VL-9-TTWO-C", "message_id": "9"},
        ]
        gravity = [{"timestamp": "2026-10-22 17:41:00", "ticker": "GLD", "signal_type": "CALL", "channel": "GRAVITY_ZONE"},
                   {"timestamp": "2026-10-22 17:41:00", "ticker": "TTWO", "signal_type": "CALL", "channel": "GRAVITY_ZONE"}]
        cs = build_candidates(signals, gravity, CFG)
        self.assertEqual([(c["ticker"], c["channel"], c["original"]) for c in cs],
                         [("FCX", "VICENTE_LUZ", "1"), ("TTWO", "VICENTE_LUZ", "0"), ("GLD", "GRAVITY_ZONE", "1")])

    def test_commission(self):
        self.assertAlmostEqual(commission_pct(0.5, CFG), 2.5)
        self.assertAlmostEqual(commission_pct(-0.5, CFG), 1.5)

    def test_black_scholes_sanity(self):
        self.assertAlmostEqual(bs_price(100, 105, 0, 0.3, 0.045, "CALL"), 0.0)
        self.assertAlmostEqual(bs_price(110, 105, 0, 0.3, 0.045, "CALL"), 5.0)


if __name__ == "__main__":
    unittest.main()
