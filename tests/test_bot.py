"""Pruebas del bot con comandos (bot/commands.py y la autorizacion de bot/server.py)."""
import importlib.util
import os
import sys
import unittest
from datetime import datetime, timezone

from _paths import ROOT

sys.path.insert(0, str(ROOT / "bot"))
import commands  # noqa: E402
from common_utils import DEFAULT_CONFIG  # noqa: E402
from test_reports import ProfitLevelsReportTest, sample_data  # noqa: E402

CFG = dict(DEFAULT_CONFIG, gravity_enabled=False)
NOW = datetime(2026, 11, 30, 15, 0, tzinfo=timezone.utc)


def data():
    d = ProfitLevelsReportTest().leveled()
    d["gravity_hist"] = [o for o in d["ops"] if o["channel"] == "GRAVITY_ZONE"]
    d["ops"] = [o for o in d["ops"] if o["channel"] != "GRAVITY_ZONE"]
    return d


def ask(text):
    return commands.respond(text, data(), CFG, NOW, {"prices_last_fetch": "2026-11-30 14:45:00"})


class CommandsTest(unittest.TestCase):
    def test_estado(self):
        r = ask("/estado")
        self.assertIn("Señales válidas: 26", r)
        self.assertIn("Retorno neto con costo bancario", r)
        self.assertIn("2026-11-30 14:45:00", r)

    def test_abiertas(self):
        r = ask("/abiertas")
        self.assertIn("Operaciones abiertas (1)", r)
        self.assertIn("#3 NVDA CALL", r)

    def test_niveles(self):
        r = ask("/niveles")
        self.assertIn("NVDA CALL +75%", r)
        self.assertIn("en sombra", r)
        self.assertLess(r.index("+75%"), r.index("+50%"))   # el mas reciente primero

    def test_senales_with_and_without_enie(self):
        self.assertIn("Últimas 10 señales válidas", ask("/senales"))
        self.assertIn("Últimas 10 señales válidas", ask("/señales"))
        self.assertIn("Repetición de un contrato", ask("/senales"))

    def test_aciertos(self):
        r = ask("/aciertos")
        self.assertIn("NVDA: take-profit 1/2 (50%)", r)
        self.assertIn("no es confiable", r)

    def test_help_and_bot_suffix(self):
        self.assertEqual(ask("/ayuda"), commands.HELP)
        self.assertEqual(ask("hola"), commands.HELP)
        self.assertIn("Señales válidas", ask("/estado@senales_papel_bot"))

    def test_messages_fit_telegram(self):
        for c in ("/estado", "/abiertas", "/niveles", "/senales", "/aciertos", "/ayuda"):
            self.assertLessEqual(len(ask(c)), 4000)


class ServerAuthTest(unittest.TestCase):
    """El servidor solo responde al chat autorizado."""

    @classmethod
    def setUpClass(cls):
        saved = os.environ.get("SENALES_ROOT")
        spec = importlib.util.spec_from_file_location("bot_server", ROOT / "bot" / "server.py")
        cls.srv = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.srv)
        if saved is None:
            os.environ.pop("SENALES_ROOT", None)
        else:
            os.environ["SENALES_ROOT"] = saved
        cls.srv.refresh_data = lambda force=False: None
        cls.srv.load_data = lambda cfg=None: data()
        cls.srv.get_config = lambda: CFG
        cls.srv.load_state = lambda: {}

    def update(self, chat, text="/estado"):
        return {"message": {"chat": {"id": chat}, "text": text}}

    def test_only_owner_gets_answers(self):
        os.environ["TELEGRAM_CHAT_ID"] = "123"
        try:
            r = self.srv.reply_for(self.update(123))
            self.assertEqual((r["method"], r["chat_id"]), ("sendMessage", "123"))
            self.assertIn("Señales válidas", r["text"])
            self.assertIsNone(self.srv.reply_for(self.update(999)))
        finally:
            os.environ.pop("TELEGRAM_CHAT_ID", None)

    def test_no_chat_id_configured_answers_nobody(self):
        os.environ.pop("TELEGRAM_CHAT_ID", None)
        self.assertIsNone(self.srv.reply_for(self.update(123)))

    def test_webhook_secret_is_telegram_safe(self):
        os.environ["WEBHOOK_SECRET"] = "ab+c/d=="
        try:
            sec = self.srv.webhook_secret()
            self.assertRegex(sec, r"^[0-9a-f]{64}$")
        finally:
            os.environ.pop("WEBHOOK_SECRET", None)


if __name__ == "__main__":
    unittest.main()
