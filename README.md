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

- `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` y `TELEGRAM_SESSION` (ver `scripts/0_make_session.py`).
- `SMTP_HOST`, `SMTP_USER`, `SMTP_PASS` y `ALERT_EMAIL_TO`.

El log enmascara cualquier dirección de correo.
