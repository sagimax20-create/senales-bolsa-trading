"""Script 6 — Generador de Reporte Semanal.

Agrega datos de los últimos 7 días, genera un reporte HTML y envía por email.
Se ejecuta cada viernes a las 4:00 PM ET (cierre de NYSE).

Credenciales:
  SMTP_HOST, SMTP_USER, SMTP_PASS, ALERT_EMAIL_TO (requeridas)
"""
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common_utils import get_config, load_csv, log_event, SIGNALS_CSV, SIGNALS_COLS

def generate_weekly_report() -> int:
    """Genera reporte semanal y lo envía por email."""
    try:
        cfg = get_config()

        # Leer datos
        signals = load_csv(SIGNALS_CSV, SIGNALS_COLS)

        if not signals:
            log_event("Script 6: Sin señales esta semana", "WARNING")
            return 0

        # Filtrar últimos 7 días
        now = datetime.utcnow()
        week_ago = now - timedelta(days=7)

        weekly_signals = []
        for s in signals:
            try:
                ts = datetime.fromisoformat(s["timestamp"].replace("Z", "+00:00"))
                if ts >= week_ago:
                    weekly_signals.append(s)
            except (ValueError, KeyError):
                continue

        # Estadísticas
        total = len(weekly_signals)
        calls = sum(1 for s in weekly_signals if s.get("signal_type") == "CALL")
        puts = sum(1 for s in weekly_signals if s.get("signal_type") == "PUT")

        tickers = {}
        for s in weekly_signals:
            t = s.get("ticker", "UNKNOWN")
            tickers[t] = tickers.get(t, 0) + 1

        top_ticker = max(tickers.items(), key=lambda x: x[1])[0] if tickers else "N/A"

        # Generar HTML
        html_content = f"""
        <html>
        <head>
            <meta charset="UTF-8">
            <title>Reporte Semanal - Señales de Bolsa</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; background-color: #f5f5f5; }}
                .container {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
                h1 {{ color: #333; border-bottom: 3px solid #0066cc; padding-bottom: 10px; }}
                .stats {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin: 20px 0; }}
                .stat-box {{ background: #f0f7ff; padding: 15px; border-radius: 5px; text-align: center; border-left: 4px solid #0066cc; }}
                .stat-value {{ font-size: 28px; font-weight: bold; color: #0066cc; }}
                .stat-label {{ color: #666; margin-top: 5px; }}
                .calls {{ border-left-color: #00aa00; }}
                .calls .stat-value {{ color: #00aa00; }}
                .puts {{ border-left-color: #cc0000; }}
                .puts .stat-value {{ color: #cc0000; }}
                table {{ width: 100%; border-collapse: collapse; margin: 20px 0; }}
                th {{ background: #0066cc; color: white; padding: 12px; text-align: left; }}
                td {{ padding: 10px; border-bottom: 1px solid #ddd; }}
                tr:hover {{ background: #f9f9f9; }}
                .footer {{ margin-top: 20px; color: #999; font-size: 12px; text-align: center; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>📊 Reporte Semanal - Señales de Opciones</h1>

                <p><strong>Período:</strong> {week_ago.strftime('%Y-%m-%d')} a {now.strftime('%Y-%m-%d')}</p>

                <div class="stats">
                    <div class="stat-box">
                        <div class="stat-value">{total}</div>
                        <div class="stat-label">Señales Totales</div>
                    </div>
                    <div class="stat-box calls">
                        <div class="stat-value">{calls}</div>
                        <div class="stat-label">CALLS</div>
                    </div>
                    <div class="stat-box puts">
                        <div class="stat-value">{puts}</div>
                        <div class="stat-label">PUTS</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-value">{top_ticker}</div>
                        <div class="stat-label">Ticker Principal</div>
                    </div>
                </div>

                <h2>📈 Top Tickers</h2>
                <table>
                    <tr>
                        <th>Ticker</th>
                        <th>Cantidad de Señales</th>
                        <th>Porcentaje</th>
                    </tr>
        """

        sorted_tickers = sorted(tickers.items(), key=lambda x: x[1], reverse=True)[:5]
        for ticker, count in sorted_tickers:
            pct = (count / total * 100) if total > 0 else 0
            html_content += f"""
                    <tr>
                        <td><strong>{ticker}</strong></td>
                        <td>{count}</td>
                        <td>{pct:.1f}%</td>
                    </tr>
            """

        html_content += """
                </table>

                <div class="footer">
                    <p>Reporte generado automáticamente por Señales Bolsa Trading</p>
                    <p>© 2026 - Análisis de opciones NYSE en tiempo real</p>
                </div>
            </div>
        </body>
        </html>
        """

        # Guardar HTML
        report_dir = Path("reports")
        report_dir.mkdir(exist_ok=True)
        report_file = report_dir / f"weekly_report_{now.strftime('%Y-%m-%d')}.html"
        report_file.write_text(html_content)

        # Enviar email
        smtp_host = os.getenv("SMTP_HOST")
        smtp_user = os.getenv("SMTP_USER")
        smtp_pass = os.getenv("SMTP_PASS")
        alert_email = os.getenv("ALERT_EMAIL_TO")

        if all([smtp_host, smtp_user, smtp_pass, alert_email]):
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart

            try:
                msg = MIMEMultipart("alternative")
                msg["Subject"] = f"📊 Reporte Semanal - Señales ({now.strftime('%Y-%m-%d')})"
                msg["From"] = smtp_user
                msg["To"] = alert_email

                msg.attach(MIMEText(html_content, "html"))

                with smtplib.SMTP_SSL(smtp_host, 465) as server:
                    server.login(smtp_user, smtp_pass)
                    server.sendmail(smtp_user, alert_email, msg.as_string())

                log_event(f"Script 6: Reporte semanal enviado a {alert_email}")
            except Exception as e:
                log_event(f"Script 6: Error al enviar email: {e}", "ERROR")
        else:
            log_event("Script 6: Credenciales SMTP incompletas, reporte no enviado", "WARNING")

        log_event(f"Script 6: Reporte semanal generado ({total} señales)")
        return 0

    except Exception as e:
        log_event(f"Script 6 falló: {type(e).__name__}: {e}", "ERROR")
        return 1

if __name__ == "__main__":
    sys.exit(generate_weekly_report())
