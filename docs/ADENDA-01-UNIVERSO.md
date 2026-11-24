# Adenda 01 — Universo de tickers de Vicente Luz

| | |
|---|---|
| Fecha | 2026-11-24 |
| Estado | Aprobada por el responsable del protocolo el 2026-11-24 |
| Protocolo de referencia | `PROTOCOLO.md`, congelado el 2026-10-04. SHA-256 en [`PROTOCOLO.sha256`](../PROTOCOLO.sha256). El protocolo **no se modifica**: esta adenda es la "documentación adicional" que pide su §10, nota 1. |

## Cambio

| | Protocolo §1 | Desde esta adenda |
|---|---|---|
| Vicente Luz | 8 tickers: GDX, NVDA, MARA, GLD, INTC, MSFT, DIS, FCX | Toda señal **válida** de un ticker que cotiza en EE. UU. (acciones y ETF de `data/ref/us_symbols.csv`, más los índices de Cboe SPX, XSP, NDX y RUT) |
| Gravity Zone | Los mismos 8 tickers | Sin cambio |
| Vigencia | — | Retroactiva a la primera señal capturada (2026-10-05), con los mensajes reprocesados por el parser v2 |

Todo lo demás sigue igual: strike OTM 95%, 45 días, $1,000 por señal, salidas en +50% / −50% / vencimiento, comisión 1%, costo bancario 18%, hitos 100 y 300, y rechazo si el retorno neto es menor a 10%.

Decisiones tomadas junto con esta adenda:
- **Hitos por canal.** El §1 dice "Vicente Luz + Gravity Zone (paralelo)". Cada canal tiene su conteo hacia 100 y 300 y su propio veredicto. El total combinado se muestra solo como referencia.
- **Subconjunto original.** Los reportes siguen mostrando por separado el resultado de Vicente Luz en los 8 tickers originales, hasta la decisión final.

## Motivo

- **El ritmo que suponía el protocolo no se cumple con 8 tickers.** El §5 y el §11 suponían unas 100 señales en 8 semanas (12 a 15 por semana). Con los 8 tickers, en las primeras 7 semanas se estimaron solo unas 15 a 20 señales probables, es decir 2 a 3 por semana. A ese ritmo la alarma de 100 llegaría en unos 8 a 10 meses y la decisión de 300 en unos 2 años y medio. La cifra exacta se conoce recién al reprocesar los mensajes.
- **No había resultados válidos en ningún universo.** El parser v1 contaba palabras como tickers, abría operaciones duplicadas y con mensajes que no eran señales, y tenía un solo precio por día (ver [`BITACORA-IMPLEMENTACION.md`](BITACORA-IMPLEMENTACION.md)).

## Análisis de sesgo

1. **Cuándo se decidió.** La ampliación se decidió el 2026-11-24, a 7 semanas de empezar. Para entonces el responsable ya había visto el resultado de la v1 en los 8 tickers (−53.8% con costo bancario). Ese resultado no era válido, pero existe el riesgo de que ampliar sea una forma de "huir de un mal resultado". **Mitigación:** el resultado de los 8 tickers originales se sigue calculando y mostrando aparte hasta la decisión final, y el veredicto presenta ambos.
2. **No se eligió viendo resultados.** Los retornos de las señales de otros tickers nunca se calcularon antes de decidir, porque la v1 solo operaba los 8.
3. **Criterio de la decisión.** La decisión responde a la frecuencia de señales, es decir al tamaño de la muestra, no al retorno.
4. **Reduce el sesgo de selección original.** Los 8 tickers se eligieron por su alto acierto histórico. `TICKERS-PARA-SEGUIMIENTO-GRAVITY.md` advierte que "parte del acierto alto es suerte, y suele bajar en datos nuevos". Con el universo completo se mide al canal tal como publica.
5. **Riesgos nuevos:**
   - **Opciones poco líquidas.** El modelo Black-Scholes supone que existe una opción OTM 95% a 45 días con una prima justa. En tickers chicos o con poco volumen de opciones, el error del modelo es mayor.
   - **ETF apalancados.** Pesan más los ETF apalancados y muy volátiles (NVDL, TQQQ, etc.).
   - **Señales sin precio.** Si Yahoo no tiene precios de un ticker, su señal queda "sin precio": no entra en el retorno, pero se informa en cada reporte.
6. **Validez estadística (§8).** El margen de error del §8 supone señales independientes. Por eso un contrato que se repostea cuenta una sola vez (ver la bitácora).

## Dónde se implementa

- `config.json`: `vicente_universe = "us_listed"`. `allowed_tickers` queda como el universo original y el de Gravity Zone.
- `scripts/ops_engine.py` (`build_candidates`): abre una operación por cada señal válida de Vicente Luz y por cada alerta de Gravity Zone en los 8 tickers.
- `scripts/report_lib.py`: hitos por canal y la vista "solo los 8 tickers originales".
