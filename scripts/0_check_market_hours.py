"""Script de validación: Detecta automáticamente si es horario de mercado NYSE.

Detecta EDT/EST automáticamente, valida día laboral (lunes-viernes),
y verifica que sea horario de apertura/cierre.

NYSE: 9:30 AM - 4:00 PM ET (excluye fines de semana y feriados)
- EDT (Daylight): ~Marzo 12 - Noviembre 5 (UTC-4)
- EST (Standard): ~Noviembre 5 - Marzo 12 (UTC-5)

Retorna:
  0 = es horario de mercado, continúa con los scripts
  1 = NO es horario de mercado, salta el procesamiento
"""
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

# Feriados NYSE en 2024-2026 (año-mes-día)
# Referencia: https://www.nyse.com/holidays-hours
MARKET_HOLIDAYS = {
    (2024, 1, 1),   # New Year's Day
    (2024, 1, 15),  # MLK Day
    (2024, 2, 19),  # Presidents Day
    (2024, 3, 29),  # Good Friday
    (2024, 5, 27),  # Memorial Day
    (2024, 6, 19),  # Juneteenth
    (2024, 7, 4),   # Independence Day
    (2024, 9, 2),   # Labor Day
    (2024, 11, 28), # Thanksgiving
    (2024, 12, 25), # Christmas

    (2025, 1, 1),   # New Year's Day
    (2025, 1, 20),  # MLK Day
    (2025, 2, 17),  # Presidents Day
    (2025, 4, 18),  # Good Friday
    (2025, 5, 26),  # Memorial Day
    (2025, 6, 19),  # Juneteenth
    (2025, 7, 4),   # Independence Day
    (2025, 9, 1),   # Labor Day
    (2025, 11, 27), # Thanksgiving
    (2025, 12, 25), # Christmas

    (2026, 1, 1),   # New Year's Day
    (2026, 1, 19),  # MLK Day
    (2026, 2, 16),  # Presidents Day
    (2026, 4, 3),   # Good Friday
    (2026, 5, 25),  # Memorial Day
    (2026, 6, 19),  # Juneteenth
    (2026, 7, 4),   # Independence Day (Saturday, but observed Friday 7/3)
    (2026, 7, 3),   # Independence Day observed
    (2026, 9, 7),   # Labor Day
    (2026, 11, 26), # Thanksgiving
    (2026, 12, 25), # Christmas
}

def is_market_open() -> bool:
    """Retorna True si estamos en horario de mercado NYSE, False en caso contrario."""

    # Obtén la hora actual en zona horaria de Nueva York (detecta automáticamente EDT/EST)
    ny_tz = ZoneInfo("America/New_York")
    now = datetime.now(tz=ny_tz)

    # Verificar: ¿Es fin de semana? (5=sábado, 6=domingo)
    if now.weekday() >= 5:
        print(f"[MARKET_CHECK] Fin de semana ({now.strftime('%A')}): Bolsa cerrada")
        return False

    # Verificar: ¿Es feriado?
    date_tuple = (now.year, now.month, now.day)
    if date_tuple in MARKET_HOLIDAYS:
        print(f"[MARKET_CHECK] Feriado NYSE ({now.strftime('%B %d, %Y')}): Bolsa cerrada")
        return False

    # Verificar: ¿Es hora de apertura? (9:30 AM - 4:00 PM ET)
    market_open = 9 * 60 + 30  # 9:30 AM en minutos
    market_close = 16 * 60      # 4:00 PM en minutos
    current_minutes = now.hour * 60 + now.minute

    if current_minutes < market_open:
        print(f"[MARKET_CHECK] Bolsa aún no abierta: {now.strftime('%I:%M %p %Z')} (abre a las 9:30 AM)")
        return False

    if current_minutes >= market_close:
        print(f"[MARKET_CHECK] Bolsa cerrada: {now.strftime('%I:%M %p %Z')} (cierra a las 4:00 PM)")
        return False

    # ¡Es horario de mercado!
    tz_name = "EDT" if now.dst() else "EST"  # Detecta automáticamente EDT/EST
    print(f"[MARKET_CHECK] ✓ Horario de mercado NYSE: {now.strftime('%I:%M %p')} {tz_name} ({now.strftime('%A, %B %d, %Y')})")
    return True

if __name__ == "__main__":
    if is_market_open():
        print("[MARKET_CHECK] → Continuando con el procesamiento de señales...")
        sys.exit(0)
    else:
        print("[MARKET_CHECK] → Saltando procesamiento (bolsa cerrada)")
        sys.exit(1)
