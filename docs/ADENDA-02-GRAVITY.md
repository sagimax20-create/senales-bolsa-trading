# Adenda 02 — Se suspende el canal Gravity Zone

| | |
|---|---|
| Fecha | 2026-11-24 |
| Estado | Aprobada por el responsable del protocolo el 2026-11-24, a partir de una recomendación de Cowork (asistente de IA) |
| Protocolo de referencia | `PROTOCOLO.md`, congelado el 2026-10-04 (SHA-256 en [`PROTOCOLO.sha256`](../PROTOCOLO.sha256)). No se modifica: esta adenda es la documentación que pide su §10, nota 1. Complementa la [adenda 01](ADENDA-01-UNIVERSO.md). |

## Cambio

| | Protocolo §1 | Desde esta adenda |
|---|---|---|
| Canales del testeo | Vicente Luz + Gravity Zone (paralelo) | **Solo Vicente Luz** |

- No se generan más alertas de Gravity Zone. En `config.json`, `gravity_enabled` pasa a `false`, y `scripts/2_gravity_alerts.py` no evalúa nada.
- **No se borra nada.** `data/gravity_alerts.csv` (3 alertas) y las 3 operaciones de Gravity Zone en `data/operations.csv` se conservan como histórico, sin recalcularse.
- Hitos (100 y 300), resultados y veredicto: solo Vicente Luz. Los reportes muestran una nota con el histórico de Gravity Zone.
- Vicente Luz no cambia en nada: universo (adenda 01), reglas, costos, hitos y criterio de rechazo siguen igual.

## Resultado de Gravity Zone al suspenderse

| Alerta (cierre) | Ticker | Tipo | Entrada | Salida | Neto |
|---|---|---|---|---|---|
| 2026-10-22 | GLD | CALL | 23-oct, apertura | Stop-loss, 30-oct | −51.5% |
| 2026-10-23 | GLD | CALL | 26-oct, apertura | Stop-loss, 30-oct | −51.5% |
| 2026-10-26 | GLD | CALL | 27-oct, apertura | Stop-loss, 30-oct | −51.5% |

Son 3 alertas en 7 semanas, con 8 tickers.

## Motivo

1. **Nunca se testeó el indicador real.** El pipeline no tenía acceso al indicador Gravity Zone de TradingView. Usaba una aproximación: z-score de 20 días más un filtro de rango del día, con umbrales que puso la implementación original y que no están en el protocolo. Sus resultados no dicen nada sobre el indicador real.
2. **Muestra insuficiente.** La aproximación da unas 6 alertas al mes con 8 tickers. Llegar a 100 alertas tomaría más de un año, lejos del plazo del protocolo (§5, §11).
3. **El indicador real no está conectado.** No hay alertas de TradingView configuradas, y la página del indicador no fue accesible desde el entorno de trabajo.

## Análisis de sesgo

1. **Cuándo se decidió.** La suspensión se decidió después de ver los resultados de Gravity Zone: 3 operaciones, las 3 con stop-loss. Existe el riesgo de que se quite un canal perdedor para mejorar el resultado total. **Mitigación:** desde la adenda 01, los hitos y el veredicto ya se calculan por canal, así que Gravity Zone nunca se mezcló con Vicente Luz. Sus resultados quedan registrados aquí y en el repo.
2. **No se pierde información sobre el indicador real.** Lo que se suspende es la imitación, no el indicador. Su resultado no era evidencia a favor ni en contra de Gravity Zone.
3. **Vicente Luz no se ve afectado.** Sus señales, operaciones y criterio de decisión no dependen de Gravity Zone.

## Cómo reanudarlo

Volver a testear Gravity Zone requiere una nueva adenda. Lo recomendable es hacerlo con el indicador real, mediante alertas de TradingView enviadas a Telegram o a correo, y no con la aproximación. Técnicamente basta con poner `gravity_enabled` en `true`.
