# Mensajes reales para las pruebas del parser

`mensajes_reales.json` contiene **líneas de alerta reales** del canal, copiadas por el
responsable del protocolo el 2026-11-24. Solo se guardan las líneas de alerta, cortas; los
comentarios largos y los textos explicativos se usaron para verificar el parser pero no se
publican.

Cada elemento:

```json
{
  "message_id": null,
  "fecha": "2026-10-19",
  "texto": "línea(s) de alerta tal como aparecen en Telegram",
  "esperado": [{"ticker": "NVDA", "tipo": "CALL", "estado": "valida",
                "strike": 225, "vencimiento": "2026-10-23", "prima": 2.19}]
}
```

- `fecha`: la del mensaje según la tabla con la que se pidieron (hora de Lima). `null` = sin
  dato; entonces la prueba no compara el vencimiento.
- `message_id`: `null` porque Telegram no lo muestra al copiar.
- `esperado`: las señales válidas que debe dar el mensaje (vacía si ninguna). `strike`,
  `vencimiento` y `prima` son opcionales: si están, también se comparan.

`tests/test_parser.py` prueba además mensajes **reconstruidos** con las palabras que guardó la
versión 1 (los números de esos son ilustrativos).
