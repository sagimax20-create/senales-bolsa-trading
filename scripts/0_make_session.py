"""Ayudante de un solo uso: genera el TELEGRAM_SESSION (StringSession) para GitHub Secrets.

GitHub Actions no puede iniciar sesion de forma interactiva, asi que la sesion se crea UNA
vez en tu computadora:

    py scripts/0_make_session.py

Te pide telefono y codigo de Telegram. Copia la cadena que imprime a un Secret llamado
TELEGRAM_SESSION. Esa cadena equivale a tu sesion de Telegram: no la publiques ni la
commitees. Requiere TELEGRAM_API_ID y TELEGRAM_API_HASH (de https://my.telegram.org).
"""
import os

from telethon.sessions import StringSession
from telethon.sync import TelegramClient

api_id = int(os.getenv("TELEGRAM_API_ID") or input("API_ID: "))
api_hash = os.getenv("TELEGRAM_API_HASH") or input("API_HASH: ")
with TelegramClient(StringSession(), api_id, api_hash) as client:
    print("\nTELEGRAM_SESSION =\n" + client.session.save())
