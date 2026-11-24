# Bitácora de implementación

Esta bitácora registra las correcciones al código que implementa el protocolo y las interpretaciones de lo que el protocolo no define. `PROTOCOLO.md` no cambia; su huella SHA-256 está en [`PROTOCOLO.sha256`](../PROTOCOLO.sha256). Los cambios al protocolo mismo van en adendas: [`ADENDA-01-UNIVERSO.md`](ADENDA-01-UNIVERSO.md).

## 2026-11-24 — Parser v4 (mensajes reales del canal)

El responsable del protocolo copió 11 mensajes reales. El parser v3 acertó en 7 y falló en 4. Cada fallo reveló un formato real del canal:

| Mensaje real | Error en v3 | Corrección (v4) |
|---|---|---|
| "October 16 CVX $215 calls" | No encontró el ticker porque está entre la fecha y el strike | Se busca el ticker también dentro del contrato. |
| "$NVDA buy 23 OCT $225 Call" | Vencimiento 2026-10 en vez de 2026-10-23 | El día antes del mes ("23 OCT", "9 NOV 2026") cuenta como día. |
| "Buy to Open: 23 OCT 26 28920 NDX Put / Sell to Open: 23 OCT 26 28930 NDX Put" (mensaje "Bullish") | Se registró PUT; "26" se leyó como día; no tomó el strike | Año de 2 dígitos tras "día MES"; strike antes del ticker ("28920 NDX Put"). **Spreads:** si el mensaje compra y vende el mismo tipo del mismo ticker, es una señal con la dirección del spread. Comprar el strike menor y vender el mayor es alcista (CALL); al revés, bajista (PUT). Este spread de crédito con puts es CALL. La pierna vendida queda `pierna_de_spread`; una venta sola, `venta_de_opciones` (el protocolo solo compra). |
| "BUY XSP … $727 Call / … / BUY SPX … $7270 Call" (24 Hour Payout Plan) | Dos señales | XSP y SPX son el mismo índice (XSP = SPX/10): en el mismo mensaje y con la misma dirección cuentan una vez (`mismo_subyacente`). |

Las líneas de alerta reales quedaron como pruebas en `tests/fixtures/mensajes_reales.json`. Nueva columna en `signals.csv`: `spread`.

Efecto esperado en los datos: la señal NDX del 23-oct pasa de PUT a CALL, CVX del 7-oct entra como señal, y los vencimientos con "día MES" quedan completos. La próxima corrida reprocesa todo, porque `parser_version` pasa a 4.

## 2026-11-24 — Precios para tickers nuevos fuera de horario

Con la bolsa cerrada, el script 3 solo descarga cada 4 horas. En la corrida del parser v3 (04:45 UTC) eso dejó 38 de 150 operaciones sin precio: 25 tickers nuevos (FCX, NVDL, TTWO, DIA…) sin ninguna vela. Ahora, durante esa pausa, igual se descargan los tickers que aún no tienen velas; el resto sigue esperando.

## 2026-11-24 — Parser v3 (corrección tras la primera corrida real)

La primera corrida real de la v2 (04:23 UTC) leyó 1,385 mensajes desde el 4-oct, encontró 82 señales válidas y terminó sin errores. Al revisar sus resultados aparecieron tres problemas:

| Problema | Evidencia | Corrección (v3) |
|---|---|---|
| El canal escribe muchos tickers en minúsculas ("Fcx jan 50 calls") y la v2 solo aceptaba MAYÚSCULAS | 128 mensajes con un ticker sin ambigüedad (NVDL, FCX, DIA, TTWO, TQQQ, NVDA, IWM…) quedaron descartados como "palabra en minúsculas"; 102 de ellos traían mes de vencimiento | Una palabra en minúsculas cuenta como ticker solo si: está pegada al contrato, tiene 3 letras o más, está en la lista de símbolos, la línea no trae otro ticker en mayúsculas, el mensaje trae strike, y ese strike está entre 0.5 y 2 veces el cierre real del ticker ese día. Así "Grab Jan 50 calls" no se lee como GRAB (~$5). Si falla, queda descartada con motivo `minusculas_sin_strike`, `minusculas_sin_precio` o `minusculas_strike_incoherente`. Nueva columna `ticker_escrito` (mayusculas / cashtag / minusculas). |
| El día de vencimiento se tomaba como strike | "INTC Nov 20 calls" dio strike 20 con INTC en ~$92. Pasó igual en MSFT, MSTR, KTOS, TQQQ, RKLB, MCD, USO y T. En "VST Jan 2027 calls", el strike salió 2027 | Un número pegado al mes y a la dirección es el día si ese día es viernes, o si hay otro número antes del mes que sirva de strike ("INTC 40 Nov 20 calls"). Un año (2025–2031) nunca es strike. |
| "Nov 20TH" se leyó como el ticker TH | Señal de TH con strike 80 y TH en ~$15 | Los ordinales (1st, 2nd, 20th) se leen como números. |

La siguiente corrida del monitor reprocesa todo automáticamente, porque cambia `parser_version`. Antes copia la v2 a `data/backup/` y vuelve a auditar las filas de la v1 contra `data/backup/signals_v1_*.csv`. La versión de operaciones no cambia: se conservan las operaciones de señales que siguen válidas, se agregan las nuevas y se quitan las de señales que ya no lo son.

Otro dato de esa corrida: 448 de los 1,385 mensajes no tienen texto (imágenes o archivos sin descripción). Si una alerta se publica solo como imagen, el pipeline no la puede leer.

## 2026-11-24 — Versión 2 del pipeline

### Problemas de la versión 1 (diagnóstico del 2026-11-24)

| Problema | Efecto |
|---|---|
| El parser pasaba el texto a MAYÚSCULAS antes de buscar tickers | Toda palabra de 2 a 5 letras se volvía ticker (THE, FOR, AT, PER, DEC, NOV…). Resultado: 2,304 "señales" de 364 mensajes en 7 semanas; solo 316 filas eran tickers sin ambigüedad. |
| Bastaba la palabra "call" en cualquier parte del mensaje | Todas las palabras de todas las líneas heredaban la dirección: comentarios largos daban hasta 73 señales. |
| No reconocía la notación "200C", tickers de 1 letra ni la prima escrita "at 2.35" | Alertas reales que se perdieron sin registro. |
| Los reposteos contaban como señales nuevas | Por ejemplo, FCX publicado dos veces en 8 segundos: 14 de 37 operaciones eran copias exactas. |
| GitHub corrió el monitor unas 2 veces por día, no cada 15 minutos | Solo hubo 34 capturas de precio en 7 semanas. La entrada se fijaba hasta ~24 h tarde y el +50% / −50% se revisaba una vez al día. |
| El filtro de horario salía con código 1 | 45 corridas en rojo eran falsas alarmas y escondían el único fallo real. |
| El workflow mensual tenía un error de sintaxis (`>> $GITHUB_OUTPUT` en un paso Python) | No llegó ningún reporte mensual. |
| El reporte semanal solo contaba filas de signals.csv | No mostraba operaciones, reglas de salida ni retornos. |
| El log público guardaba la dirección de correo de destino | 7 apariciones en `data/log.txt`. |
| La nota 2 del §10 no se cumplía | El seguimiento se detenía al tocar +50%. |

### Correcciones

| Archivo | Cambio |
|---|---|
| `scripts/signal_parser.py` (nuevo) | Parser v2: lee el texto como fue escrito, exige que el ticker esté en la lista de EE. UU., y exige estructura de señal en la misma línea (dirección + strike, vencimiento o prima). El ticker es el más cercano antes del contrato. Descarta cierres y ventas, direcciones ambiguas y reposteos, y extrae strike, vencimiento y prima. No usa listas de palabras excluidas. |
| `scripts/1_parser_signals.py` | Lee Telegram en orden cronológico, sin el límite de 200. La primera vez reprocesa todo desde `capture_start`, copiando antes `signals.csv` a `data/backup/`. Escribe `signals.csv` (con estado y motivo), `messages.csv` (cobertura: un SHA-256 por mensaje, nunca el texto) y `signals_v1_audit.csv` (cada fila de la v1 marcada válida o descartada, con motivo). Enmascara los correos del log. |
| `data/ref/us_symbols.csv` + `scripts/0_update_symbols.py` | Lista de símbolos de EE. UU.: copia inicial armada con espejos públicos, que se actualiza cada semana desde `nasdaqtraded.txt` de Nasdaq Trader. |
| `scripts/3_fetch_prices.py` | Velas de 15 minutos por ticker (de 60 minutos para tramos con más de 60 días), con relleno de huecos. La frecuencia real de GitHub deja de importar. |
| `scripts/ops_engine.py` (nuevo) + `scripts/4_calculate_returns.py` | Operaciones solo con señales válidas, evaluadas vela por vela, con seguimiento en sombra hasta el día 45 (nota 2). La primera vez se respalda el `operations.csv` de la v1 y se reconstruye. |
| `scripts/report_lib.py` (nuevo), `5_…`, `6_…`, `7_…` | Hitos por canal. Reporte semanal los lunes, con hitos, descartes, resultado por regla, retorno neto con comisión y costo bancario, y tablas de operaciones. Reporte mensual el último día del mes, con análisis por ticker, canal y tipo; los meses que falten se envían marcados "tardío" (incluido octubre). |
| `common_utils.py` | Feriados NYSE 2026-2027 dentro de `market_open`; filtro que enmascara correos en el log. |
| Workflows | El monitor corre 24/7 (§7) en los minutos 7/22/37/52, sin el paso que marcaba fallos falsos. Semanal: lunes, con el martes de respaldo. Mensual: días 28-31 y día 1 de respaldo. Pruebas en cada PR. |
| `scripts/0_check_market_hours.py` | Eliminado: sus feriados pasaron a `common_utils`. |

### Interpretaciones del protocolo (aprobadas el 2026-11-24)

| § | Qué no define el protocolo | Cómo se implementa |
|---|---|---|
| 4 | Comisión "1% por operación, descuento automático en entrada/salida" | 1% del capital al entrar más 1% del valor al salir (lectura literal y conservadora). |
| 4 | Costo bancario: qué es un "ciclo" | Todo el testeo de un canal es un ciclo. Se aplica como en el ejemplo del §4: se deposita capital / 0.92, se opera con el 92% y al retirar se descuenta el 10%. Por eso, para llegar a 10% neto hace falta ~32.9% antes del costo bancario. |
| 3 | Cómo registrar el take-profit y el stop-loss | Take-profit: exactamente +50% (orden límite). Stop-loss: −50% si se cruza dentro de la vela; si la vela abre por debajo (salto de precio), el valor de apertura. Si una misma vela toca +50% y −50%, se asume el stop-loss (supuesto conservador: con velas de 15 minutos no se sabe cuál fue primero). |
| 2 | Prima de entrada | Black-Scholes con IV = volatilidad realizada de 20 días (no hay cotizaciones reales de opciones). El contrato es el del protocolo (OTM 95%, 45 días), no el que publica el canal. |
| 2 | Momento de entrada | Apertura de la primera vela de 15 minutos que empieza en o después de la señal. Si la señal llega con la bolsa cerrada, entra en la apertura siguiente. |
| 3 | Vencimiento | Valor intrínseco con el último cierre antes del día 45. |
| 5 | Qué cuenta como "señal" | Vicente Luz: señal válida del parser v2. Gravity Zone: alerta. Conteo por canal. |
| 6 | "Capital inicial" | Lo depositado: $1,000 × operaciones cerradas / 0.92. |
| 5 | Reposteos | Cuenta la primera aparición de un contrato. Una señal posterior del mismo ticker y dirección, sin datos que se contradigan (strike o vencimiento distintos) y dentro de 45 días, queda como "repetición". |

### Desviaciones conocidas

- **§7, base de datos.** El protocolo dice Supabase o SQLite; se usan archivos CSV versionados en git. Esto no cambia los resultados y deja rastro de cada cambio.
- **§7, frecuencia.** GitHub Actions no garantiza correr cada 15 minutos. Lo compensan el cursor de Telegram (no se pierden mensajes) y las velas de 15 minutos (no se pierden tramos de precio).
- **Gravity Zone.** Sigue siendo una aproximación (z-score diario), no el indicador original.
- **`iv_fallback` de `config.json`.** No coincide con la tabla de volatilidad de `TICKERS-PARA-SEGUIMIENTO-GRAVITY.md`. Solo se usa sin historia diaria, cosa que con Yahoo no pasa en la práctica. No se cambió.
- **Límites del parser:**
  - Un ticker en minúsculas sin `$` solo se reconoce pegado al contrato y con un strike coherente con su precio (v3).
  - Si una línea trae un ticker en mayúsculas y otro en minúsculas, gana el de mayúsculas.
  - Las alertas publicadas solo como imagen no se leen.
  - Un tutorial con un contrato de ejemplo bien escrito puede pasar como señal.
  - "MAR" en mayúsculas se lee como marzo, no como Marriott.
  - Cada caso queda en `signals.csv` o en `messages.csv` con su motivo, para auditarlo.
- **Pruebas del parser.** Usan mensajes reconstruidos a partir de las palabras que guardó la v1, porque el repo nunca guardó el texto original. Los mensajes reales se agregan en `tests/fixtures/mensajes_reales.json`.
