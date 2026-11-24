"""Pruebas de reportes, costo bancario, calendario y utilidades (sin red ni correo)."""
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from _paths import load_script

from common_utils import DEFAULT_CONFIG, mask_emails, market_open, sanitize_log_file  # noqa: E402
from report_lib import bank_adjusted, milestones, summarize  # noqa: E402

CFG = dict(DEFAULT_CONFIG)
W6 = load_script("6_generate_weekly_report")
M7 = load_script("7_generate_monthly_report")
P3 = load_script("3_fetch_prices")
S0 = load_script("0_update_symbols")


def op(i, ts_entry, ts_exit, reason, net, channel="VICENTE_LUZ", estado="closed", original="0", ticker="NVDA"):
    return {"id": str(i), "channel": channel, "ticker": ticker, "option_type": "CALL", "estado": estado,
            "signal_timestamp": ts_entry, "timestamp_entry": ts_entry, "timestamp_exit": ts_exit,
            "reason_exit": reason, "retorno_neto_pct": str(net), "retorno_bruto_pct": str(net + 2),
            "comision_pct": "2", "en_universo_original": original, "signal_key": f"{channel}|VL-{i}-NVDA-C",
            "sombra_completa": "1", "toco_100": "1" if reason == "take_profit" else "0", "toco_150": "0",
            "toco_200": "0", "spot_entry": "100", "strike_usd": "105", "price_entry": "2", "price_exit": "3",
            "days_held": "3"}


def sample_data():
    valid = [{"timestamp": f"2026-11-{d:02d} 15:00:00", "ticker": "NVDA", "signal_type": "CALL",
              "channel": "VICENTE_LUZ", "estado": "valida", "signal_id": f"VL-{d}-NVDA-C", "message_id": str(d),
              "motivo": ""} for d in range(2, 28)]
    discarded = [{"timestamp": "2026-11-24 15:00:00", "ticker": "FCX", "signal_type": "CALL", "channel": "VICENTE_LUZ",
                  "estado": "descartada", "motivo": "repeticion", "signal_id": "VL-99-FCX-C", "message_id": "99"}]
    ops = [op(1, "2026-11-03 15:00:00", "2026-11-05 15:00:00", "take_profit", 47.5),
           op(2, "2026-11-04 15:00:00", "2026-11-24 16:00:00", "stop_loss", -51.5, original="1"),
           op(3, "2026-11-24 15:00:00", "", "", -10, estado="open"),
           op(4, "2026-10-22 17:41:02", "2026-10-30 18:17:19", "stop_loss", -75.4, channel="GRAVITY_ZONE")]
    return {"signals": valid + discarded, "valid": valid, "ops": ops, "messages": [],
            "gravity": [{"timestamp": "2026-10-22 17:41:00", "ticker": "GLD", "signal_type": "CALL",
                         "channel": "GRAVITY_ZONE"}]}


class BankCostTest(unittest.TestCase):
    def test_example_of_protocol_section_4(self):
        """Deposito $10,000 -> opera $9,200 -> retira $11,200 -> recibe $10,080 -> +0.8%."""
        cfg = dict(CFG, protocol_capital_per_signal=920)
        b = bank_adjusted(10, 2000, cfg)
        self.assertAlmostEqual(b["deposito"], 10000)
        self.assertAlmostEqual(b["recibe"], 10080)
        self.assertAlmostEqual(b["costo_banco"], 800 + 1120)
        self.assertAlmostEqual(b["ret_con_banco"], 0.8)

    def test_threshold_needs_about_32_9_percent_before_bank(self):
        b = bank_adjusted(100, 100 * 1000 * 0.3285, CFG)
        self.assertAlmostEqual(b["ret_con_banco"], 10.0, places=1)

    def test_summary_by_reason(self):
        st = summarize(sample_data()["ops"], CFG)
        self.assertEqual((st["n_closed"], st["n_open"]), (3, 1))
        self.assertEqual(st["by_reason"]["take_profit"]["n"], 1)
        self.assertEqual(st["by_reason"]["stop_loss"]["n"], 2)
        self.assertAlmostEqual(st["pnl_usd"], 475 - 515 - 754)


class MilestonesTest(unittest.TestCase):
    def test_counts_per_channel(self):
        data = sample_data()
        now = datetime(2026, 11, 30, 14, 0, tzinfo=timezone.utc)
        self.assertEqual(milestones(data, "VICENTE_LUZ", CFG, now)["n"], 26)
        self.assertEqual(milestones(data, "GRAVITY_ZONE", CFG, now)["n"], 1)
        self.assertIn("EN PROGRESO (26/100)", milestones(data, "VICENTE_LUZ", CFG, now)["a_state"])


class CalendarTest(unittest.TestCase):
    def test_last_complete_week(self):
        mon = datetime(2026, 11, 30, 13, 23, tzinfo=timezone.utc)
        self.assertEqual(W6.last_complete_week(mon), (date(2026, 11, 23), date(2026, 11, 30)))
        self.assertEqual(W6.last_complete_week(mon + timedelta(days=1)), (date(2026, 11, 23), date(2026, 11, 30)))

    def test_months_to_send(self):
        f = M7.months_to_send
        self.assertEqual(f(date(2026, 11, 24), set(), "2026-10-04"), [("2026-10", True)])
        self.assertEqual(f(date(2026, 11, 30), set(), "2026-10-04"), [("2026-10", True), ("2026-11", False)])
        self.assertEqual(f(date(2026, 11, 30), {"2026-10"}, "2026-10-04"), [("2026-11", False)])
        self.assertEqual(f(date(2026, 12, 1), {"2026-10"}, "2026-10-04"), [("2026-11", True)])
        self.assertEqual(f(date(2026, 12, 15), {"2026-10", "2026-11"}, "2026-10-04"), [])
        self.assertEqual(f(date(2026, 12, 15), {"2026-10"}, "2026-10-04", forced="2026-10"), [("2026-10", False)])

    def test_market_hours_and_holidays(self):
        self.assertTrue(market_open(datetime(2026, 11, 25, 15, 0, tzinfo=timezone.utc)))     # 10:00 EST
        self.assertFalse(market_open(datetime(2026, 11, 26, 15, 0, tzinfo=timezone.utc)))    # Thanksgiving
        self.assertFalse(market_open(datetime(2026, 11, 28, 15, 0, tzinfo=timezone.utc)))    # sabado
        self.assertFalse(market_open(datetime(2026, 11, 25, 22, 0, tzinfo=timezone.utc)))    # 17:00 EST


class ReportHtmlTest(unittest.TestCase):
    def test_weekly_contains_required_sections(self):
        now = datetime(2026, 11, 30, 13, 23, tzinfo=timezone.utc)
        html, text = W6.build_weekly(sample_data(), CFG, now, date(2026, 11, 23), date(2026, 11, 30))
        for s in ("Avance hacia los hitos", "Alarma", "Decisión final", "Resultado por regla de salida",
                  "Retorno neto con costo bancario", "Operaciones de la semana", "Operaciones abiertas hoy",
                  "Repetición de un contrato", "solo los 8 tickers originales", "no asesoría financiera"):
            self.assertIn(s, html)
        self.assertIn("Vicente Luz", text)
        self.assertNotIn("@", html.replace("@media", ""))

    def test_monthly_contains_analysis_by_ticker_channel_type(self):
        now = datetime(2026, 11, 30, 21, 37, tzinfo=timezone.utc)
        html, _ = M7.build_monthly(sample_data(), CFG, now, "2026-10", late=True)
        for s in ("Por canal", "Por tipo de opción", "Por ticker", "enviado con retraso", "nota 2"):
            self.assertIn(s, html)


class PrivacyTest(unittest.TestCase):
    def test_emails_are_masked(self):
        self.assertEqual(mask_emails("Reporte enviado a alguien@example.com"), "Reporte enviado a [OCULTO]")

    def test_sanitize_log_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "log.txt"
            p.write_text("a\nScript 6: Reporte semanal enviado a x.y@mail.example.org\nb\n", encoding="utf-8")
            self.assertEqual(sanitize_log_file(p), 1)
            self.assertNotIn("@", p.read_text(encoding="utf-8"))


class PricesTest(unittest.TestCase):
    NOW = datetime(2026, 11, 30, 21, 0, tzinfo=timezone.utc)

    def test_plan_uses_60m_before_window_and_15m_after(self):
        ranges = P3.plan_ranges([], datetime(2026, 9, 25, tzinfo=timezone.utc), self.NOW)
        self.assertEqual([r[0] for r in ranges], ["60m", "15m"])
        self.assertEqual(ranges[-1][2], self.NOW)

    def test_plan_continues_after_last_bar(self):
        have = [{"timestamp": "2026-11-27 14:30:00", "interval": "15m"},
                {"timestamp": "2026-11-27 20:45:00", "interval": "15m"}]
        ranges = P3.plan_ranges(have, datetime(2026, 11, 27, 15, 0, tzinfo=timezone.utc), self.NOW)
        self.assertEqual(ranges, [("15m", datetime(2026, 11, 27, 20, 45, 1, tzinfo=timezone.utc), self.NOW)])

    def test_plan_backfills_when_signal_is_older_than_stored_bars(self):
        have = [{"timestamp": "2026-11-27 20:45:00", "interval": "15m"}]
        ranges = P3.plan_ranges(have, datetime(2026, 11, 27, 15, 0, tzinfo=timezone.utc), self.NOW)
        self.assertEqual(ranges[0], ("15m", datetime(2026, 11, 26, 15, 0, tzinfo=timezone.utc),
                                     datetime(2026, 11, 27, 20, 45, tzinfo=timezone.utc)))

    def test_merge_skips_open_bar_and_overlapping_60m(self):
        have = [{"timestamp": "2026-11-30 19:00:00", "interval": "60m"}]
        new = [{"timestamp": "2026-11-30 19:30:00", "interval": "15m"},
               {"timestamp": "2026-11-30 20:50:00", "interval": "15m"}]   # termina 21:05 > ahora
        merged = P3.merge_bars(have, new, self.NOW)
        self.assertEqual([b["timestamp"] for b in merged], ["2026-11-30 19:00:00", "2026-11-30 19:30:00"])

    def test_needed_tickers(self):
        def s(ts, t, estado="valida"):
            return {"timestamp": ts, "ticker": t, "signal_type": "CALL", "estado": estado,
                    "channel": "VICENTE_LUZ", "signal_id": f"VL-1-{t}-C", "message_id": "1"}
        sig = [s("2026-11-20 15:00:00", "NVDA"), s("2026-10-05 15:00:00", "FCX"),
               s("2026-10-06 15:00:00", "TTWO"), s("2026-11-21 15:00:00", "THE", "descartada")]
        ops = [{"signal_key": "VICENTE_LUZ|VL-1-TTWO-C", "estado": "closed", "sombra_completa": "1"}]
        got = P3.needed_tickers(sig, [], CFG, self.NOW, ops)
        self.assertEqual(set(got), {"NVDA", "FCX"})   # FCX es antigua pero su operacion no termino
        self.assertEqual(got["FCX"], datetime(2026, 10, 5, 15, 0, tzinfo=timezone.utc))


class SymbolsTest(unittest.TestCase):
    def test_parse_nasdaqtraded(self):
        text = ("Nasdaq Traded|Symbol|Security Name|Listing Exchange|Market Category|ETF|Round Lot Size|"
                "Test Issue|Financial Status|CQS Symbol|NASDAQ Symbol|NextShares\n"
                "Y|NVDA|NVIDIA Corp|Q|Q|N|100|N|N||NVDA|N\n"
                "Y|SPY|SPDR S&P 500|P| |Y|100|N||SPY|SPY|N\n"
                "Y|ZZZT|Test issue|Q|Q|N|100|Y|N||ZZZT|N\n"
                "Y|ABC$A|Preferred|N| |N|100|N||ABCpA|ABC$A|N\n"
                "File Creation Time: 1124202608:31|||||||||||")
        rows, created = S0.parse_nasdaqtraded(text)
        self.assertEqual([(r["symbol"], r["etf"]) for r in rows], [("NVDA", "N"), ("SPY", "Y")])
        self.assertEqual(created, "1124202608:31|||||||||||")


if __name__ == "__main__":
    unittest.main()
