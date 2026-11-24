# Mensajes reales para las pruebas del parser

`mensajes_reales.json` es una lista vacía a propósito: el repo nunca guardó el texto de los
mensajes del canal (la versión 1 solo guardaba las palabras que confundía con tickers) y el
texto no se publica sin decisión del responsable. Cuando se agreguen mensajes reales, cada
elemento tiene esta forma y `tests/test_parser.py` los prueba automáticamente:

```json
{
  "message_id": 73323,
  "fecha": "2026-10-19",
  "texto": "texto exacto del mensaje, tal como aparece en Telegram",
  "esperado": [{"ticker": "NVDA", "tipo": "CALL", "estado": "valida"}]
}
```

`esperado` lista las señales que el mensaje debe dar (vacía si no debe dar ninguna). Mientras
tanto, `tests/test_parser.py` usa mensajes **reconstruidos**: las palabras salen de mensajes
reales (se cita su `message_id`), pero los números (strike, prima, día) son ilustrativos porque
la versión 1 no los guardó.
