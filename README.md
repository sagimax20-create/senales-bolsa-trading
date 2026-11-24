# senales-bolsa-trading

Seguimiento **en papel** de las señales de opciones del canal de Telegram "Alertas de Bolsa de Valores Vicente Luz". Gravity Zone se suspendió el 2026-11-24 (ver más abajo). Funciona solo, con GitHub Actions. Dinero ficticio: esto es análisis de datos, no asesoría financiera.

## Protocolo

- **Protocolo congelado.** `PROTOCOLO.md` se congeló el 2026-10-04. No está en el repo porque contiene datos personales: lo guarda su responsable. Para verificar que una copia es la original:

  ```bash
  sha256sum -c PROTOCOLO.sha256
  ```

  Hay que correrlo en la carpeta donde estén `PROTOCOLO.md` y `TICKERS-PARA-SEGUIMIENTO-GRAVITY.md`.
- **Cambios aprobados:**
  - [`docs/ADENDA-01-UNIVERSO.md`](docs/ADENDA-01-UNIVERSO.md): el universo de Vicente Luz se amplía a todos los tickers de EE. UU.
  - [`docs/ADENDA-02-GRAVITY.md`](docs/ADENDA-02-GRAVITY.md): se suspende Gravity Zone y solo se testea Vicente Luz. Su histórico se conserva.
- **Correcciones e interpretaciones.** Están en [`docs/BITACORA-IMPLEMENTACION.md`](docs/BITACORA-IMPLEMENTACION.md).

## Cómo corre

| Workflow | Cuándo | Qué hace |
|---|---|---|
| `monitor.yml` | Cada 15 min, 24/7 (§7) | Lista de símbolos (semanal) → Telegram → velas de precio → operaciones → hitos (el paso de Gravity Zone está suspendido) |
| `weekly_report.yml` | Lunes (martes de respaldo) | Reporte semanal por correo (§9) |
| `monthly_report.yml` | Último día del mes (día 1 de respaldo) | Reporte mensual por correo (§9); envía, marcado "tardío", cualquier mes que falte |
| `tests.yml` | En cada pull request | Pruebas del parser, del motor de operaciones y de los reportes |

## Archivos

| Archivo | Contenido |
|---|---|
| `data/signals.csv` | Señales candidatas: `estado` (valida / descartada) y `motivo` |
| `data/messages.csv` | Un registro por mensaje leído: fecha, SHA-256 del texto y motivo si no dio señal. El texto no se guarda. |
| `data/signals_v1_audit.csv` | Cada fila de la versión 1, marcada válida o descartada con motivo |
| `data/operations.csv` | Operaciones en papel: entrada, salida, regla, retornos y seguimiento en sombra (nota 2) |
| `data/prices/`, `data/prices_1d/` | Velas de 15 min y diarias por ticker |
| `data/ref/us_symbols.csv` | Símbolos que cotizan en EE. UU. |
| `data/backup/` | Copias anteriores; nada se borra |
| `reports/` | `hitos.md`, reportes semanales y mensuales en HTML |

## Pruebas

```bash
python -m unittest discover -s tests -v
```

## Reprocesar a mano

En GitHub, ir a Actions → "Signal Monitor" → Run workflow y marcar `reprocess`. El `signals.csv` actual se copia antes a `data/backup/`.

## Credenciales

Van como Secrets de GitHub, nunca en el repo:

- **Lectura del canal:** `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` y `TELEGRAM_SESSION` (ver `scripts/0_make_session.py`).
- **Correo:** `SMTP_HOST`, `SMTP_USER`, `SMTP_PASS` y `ALERT_EMAIL_TO`.
- **Bot de avisos (opcional):** `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID`.

El log enmascara cualquier dirección de correo y nunca escribe el token del bot.

## Bot de Telegram: avisos de +25%, +50%, +75% y +100%

`scripts/8_notify_telegram.py` avisa cuando una operación en papel llega a cada nivel de ganancia de la prima. El aviso incluye el precio del subyacente en ese momento y su distancia al strike del protocolo y al strike de Vicente. La regla oficial no cambia: se cierra en +50%, y los niveles posteriores se marcan "en sombra".

Para activarlo:

1. En Telegram, abre **@BotFather**, envía `/newbot`, elige nombre y usuario, y copia el **token** que te da. No lo pegues en ningún chat ni archivo.
2. Envíale cualquier mensaje a tu bot (por ejemplo `/start`).
3. Busca tu **chat id**. Abre en el navegador `https://api.telegram.org/bot<TOKEN>/getUpdates`, con tu token en lugar de `<TOKEN>`, y copia el número de `"chat":{"id": ...}`.
4. En GitHub, ve a Settings → Secrets and variables → Actions → New repository secret, y crea `TELEGRAM_BOT_TOKEN` (el token) y `TELEGRAM_CHAT_ID` (el número).

En la siguiente corrida del monitor llega un mensaje de bienvenida. Desde ahí llegan solo los niveles nuevos; el histórico está en el reporte semanal.

## Bot con comandos (Render)

`bot/server.py` responde a lo que le escribes al bot. Comandos:

| Comando | Respuesta |
|---|---|
| `/estado` | Señales válidas, avance hacia 100 y 300, operaciones y retorno neto con y sin costo bancario |
| `/abiertas` | Operaciones abiertas: entrada, strike, retorno actual, nivel máximo alcanzado y distancia al strike |
| `/niveles` | Últimos niveles +25/+50/+75/+100%, con distancia al strike del protocolo y al de Vicente |
| `/senales` (o `/señales`) | Últimas señales válidas de Vicente Luz y descartes de la semana |
| `/aciertos` | Tickers con más aciertos: % de take-profit y % que llegó al strike (mínimo 2 operaciones) |
| `/ayuda` | Lista de comandos |

- Responde **solo** al chat `TELEGRAM_CHAT_ID`; a cualquier otro chat no le contesta.
- Lee los datos públicos del repo (los que deja el monitor) y usa las mismas cuentas que los reportes.

**Despliegue, una sola vez:**

1. Entra a <https://render.com/deploy?repo=https://github.com/sagimax20-create/senales-bolsa-trading>. Render lee `render.yaml` y crea el servicio `senales-bot` en el plan gratuito.
2. Render te pide `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID`: son los mismos valores que pusiste en GitHub. `WEBHOOK_SECRET` lo genera Render.
3. Espera a que termine el despliegue, unos 2 minutos. El servidor registra el webhook y la lista de comandos solo; luego escríbele `/ayuda` al bot.

El plan gratuito "duerme" el servicio después de 15 minutos sin uso, así que la primera respuesta después de un rato puede tardar hasta un minuto. Al registrarse el webhook, la dirección `getUpdates` deja de funcionar; ya no hace falta, porque el chat id está guardado.

