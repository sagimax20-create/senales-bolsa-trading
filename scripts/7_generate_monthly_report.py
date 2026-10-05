"""Script 7 — Generador de Reporte Mensual.

Agrega datos de los últimos 30 días, genera un reporte HTML detallado y envía por email.
Se ejecuta el último día del mes a las 4:00 PM ET (cierre de NYSE).

Credenciales:
  SMTP_HOST, SMTP_USER, SMTP_PASS, ALERT_EMAIL_TO (requeridas)
"""
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common_utils import get_config, load_csv, log_event, SIGNALS_CSV, SIGNALS_COLS

def generate_monthly_report() -> int:
    """Genera reporte mensual y lo envía por email."""
    try:
        cfg = get_config()

        # Leer datos
        signals = load_csv(SIGNALS_CSV, SIGNALS_COLS)

        if not signals:
            log_event("Script 7: Sin señales este mes", "WARNING")
            return 0

        # Filtrar últimos 30 días
        now = datetime.utcnow()
        month_ago = now - timedelta(days=30)

        monthly_signals = []
        for s in signals:
            try:
                ts = datetime.fromisoformat(s["timestamp"].replace("Z", "+00:00"))
                if ts >= month_ago:
                    monthly_signals.append(s)
            except (ValueError, KeyError):
                continue

        # Estadísticas
        total = len(monthly_signals)
        calls = sum(1 for s in monthly_signals if s.get("signal_type") == "CALL")
        puts = sum(1 for s in monthly_signals if s.get("signal_type") == "PUT")
        call_pct = (calls / total * 100) if total > 0 else 0
        put_pct = (puts / total * 100) if total > 0 else 0

        tickers = {}
        for s in monthly_signals:
            t = s.get("ticker", "UNKNOWN")
            tickers[t] = tickers.get(t, 0) + 1

        # Análisis por día
        daily_stats = defaultdict(lambda: {"total": 0, "calls": 0, "puts": 0})
        for s in monthly_signals:
            try:
                ts = datetime.fromisoformat(s["timestamp"].replace("Z", "+00:00"))
                day = ts.strftime("%Y-%m-%d")
                daily_stats[day]["total"] += 1
                if s.get("signal_type") == "CALL":
                    daily_stats[day]["calls"] += 1
                else:
                    daily_stats[day]["puts"] += 1
            except (ValueError, KeyError):
                continue

        # Generar HTML
        html_content = f"""
        <html>
        <head>
            <meta charset="UTF-8">
            <title>Reporte Mensual - Señales de Bolsa</title>
            <style>
                body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 0; padding: 20px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); }}
                .container {{ background: white; padding: 30px; border-radius: 12px; box-shadow: 0 10px 40px rgba(0,0,0,0.3); max-width: 900px; margin: 0 auto; }}
                h1 {{ color: #333; border-bottom: 4px solid #667eea; padding-bottom: 15px; margin-bottom: 30px; }}
                h2 {{ color: #555; margin-top: 30px; border-left: 4px solid #667eea; padding-left: 15px; }}
                .stats {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 15px; margin: 25px 0; }}
                .stat-box {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; border-radius: 8px; text-align: center; box-shadow: 0 4px 15px rgba(102, 126, 234, 0.4); }}
                .stat-value {{ font-size: 32px; font-weight: bold; }}
                .stat-label {{ font-size: 12px; margin-top: 8px; opacity: 0.9; }}
                .stat-box.calls {{ background: linear-gradient(135deg, #00aa00 0%, #00cc00 100%); }}
                .stat-box.puts {{ background: linear-gradient(135deg, #cc0000 0%, #ff3333 100%); }}
                table {{ width: 100%; border-collapse: collapse; margin: 20px 0; }}
                th {{ background: #667eea; color: white; padding: 12px; text-align: left; font-weight: 600; }}
                td {{ padding: 12px; border-bottom: 1px solid #eee; }}
                tr:hover {{ background: #f8f9ff; }}
                .top-tickers {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 20px; margin: 20px 0; }}
                .ticker-card {{ background: #f8f9ff; padding: 15px; border-radius: 8px; border-left: 4px solid #667eea; }}
                .ticker-name {{ font-weight: bold; color: #333; font-size: 16px; }}
                .ticker-count {{ color: #667eea; font-size: 20px; font-weight: bold; }}
                .progress-bar {{ width: 100%; height: 6px; background: #eee; border-radius: 3px; margin-top: 8px; overflow: hidden; }}
                .progress-fill {{ height: 100%; background: linear-gradient(90deg, #667eea, #764ba2); }}
                .footer {{ margin-top: 30px; color: #999; font-size: 12px; text-align: center; padding-top: 20px; border-top: 1px solid #eee; }}
                .metric-row {{ display: flex; justify-content: space-between; margin: 10px 0; }}
                .metric-label {{ color: #666; }}
                .metric-value {{ font-weight: bold; color: #333; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>📈 Reporte Mensual - Señales de Opciones NYSE</h1>

                <p><strong>Período:</strong> {month_ago.strftime('%d de %B, %Y')} a {now.strftime('%d de %B, %Y')}</p>

                <div class="stats">
                    <div class="stat-box">
                        <div class="stat-value">{total}</div>
                        <div class="stat-label">Señales Totales</div>
                    </div>
                    <div class="stat-box calls">
                        <div class="stat-value">{calls}</div>
                        <div class="stat-label">CALLS ({call_pct:.0f}%)</div>
                    </div>
                    <div class="stat-box puts">
                        <div class="stat-value">{puts}</div>
                        <div class="stat-label">PUTS ({put_pct:.0f}%)</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-value">{len(tickers)}</div>
                        <div class="stat-label">Tickers Únicos</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-value">{len(daily_stats)}</div>
                        <div class="stat-label">Días Activos</div>
                    </div>
                </div>

                <h2>🎯 Top 10 Tickers</h2>
                <div class="top-tickers">
        """

        sorted_tickers = sorted(tickers.items(), key=lambda x: x[1], reverse=True)[:10]
        max_count = sorted_tickers[0][1] if sorted_tickers else 1

        for i, (ticker, count) in enumerate(sorted_tickers):
            if i % 2 == 0 and i > 0:
                html_content += "</div><div class='top-tickers'>"

            pct_width = (count / max_count) * 100
            html_content += f"""
                    <div class="ticker-card">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <span class="ticker-name">#{i+1} {ticker}</span>
                            <span class="ticker-count">{count}</span>
                        </div>
                        <div class="progress-bar">
                            <div class="progress-fill" style="width: {pct_width}%"></div>
                        </div>
                    </div>
            """

        html_content += """
                </div>

                <h2>📊 Distribución Diaria</h2>
                <table>
                    <tr>
                        <th>Fecha</th>
                        <th>Total</th>
                        <th>CALLS</th>
                        <th>PUTS</th>
                        <th>Ratio C/P</th>
                    </tr>
        """

        for day in sorted(daily_stats.keys(), reverse=True)[:10]:
            stats = daily_stats[day]
            ratio = f"{stats['calls']}/{stats['puts']}" if stats['puts'] > 0 else f"{stats['calls']}/0"
            html_content += f"""
                    <tr>
                        <td>{day}</td>
                        <td><strong>{stats['total']}</strong></td>
                        <td><span style="color: #00aa00;">●</span> {stats['calls']}</td>
                        <td><span style="color: #cc0000;">●</span> {stats['puts']}</td>
                        <td>{ratio}</td>
                    </tr>
            """

        html_content += """
                </table>

                <h2>📌 Resumen Ejecutivo</h2>
                <div style="background: #f8f9ff; padding: 15px; border-radius: 8px;">
        """

        avg_daily = total / len(daily_stats) if daily_stats else 0
        html_content += f"""
                    <div class="metric-row">
                        <span class="metric-label">Promedio de señales por día:</span>
                        <span class="metric-value">{avg_daily:.1f}</span>
                    </div>
                    <div class="metric-row">
                        <span class="metric-label">Ratio CALL/PUT:</span>
                        <span class="metric-value">{call_pct:.1f}% / {put_pct:.1f}%</span>
                    </div>
                    <div class="metric-row">
                        <span class="metric-label">Ticker más activo:</span>
                        <span class="metric-value">{sorted_tickers[0][0] if sorted_tickers else 'N/A'} ({sorted_tickers[0][1] if sorted_tickers else 0} señales)</span>
                    </div>
                </div>

                <div class="footer">
                    <p>Reporte generado automáticamente por Señales Bolsa Trading</p>
                    <p>© 2026 - Análisis avanzado de opciones NYSE en tiempo real</p>
                    <p>Próximo reporte: {(now + timedelta(days=30)).strftime('%d de %B, %Y')}</p>
                </div>
            </div>
        </body>
        </html>
        """

        # Guardar HTML
        report_dir = Path("reports")
        report_dir.mkdir(exist_ok=True)
        report_file = report_dir / f"monthly_report_{now.strftime('%Y-%m')}.html"
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
                msg["Subject"] = f"📈 Reporte Mensual - Señales ({now.strftime('%B %Y')})"
                msg["From"] = smtp_user
                msg["To"] = alert_email

                msg.attach(MIMEText(html_content, "html"))

                with smtplib.SMTP_SSL(smtp_host, 465) as server:
                    server.login(smtp_user, smtp_pass)
                    server.sendmail(smtp_user, alert_email, msg.as_string())

                log_event(f"Script 7: Reporte mensual enviado a {alert_email}")
            except Exception as e:
                log_event(f"Script 7: Error al enviar email: {e}", "ERROR")
        else:
            log_event("Script 7: Credenciales SMTP incompletas, reporte no enviado", "WARNING")

        log_event(f"Script 7: Reporte mensual generado ({total} señales)")
        return 0

    except Exception as e:
        log_event(f"Script 7 falló: {type(e).__name__}: {e}", "ERROR")
        return 1

if __name__ == "__main__":
    sys.exit(generate_monthly_report())
