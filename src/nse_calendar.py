"""NSE equity / equity-derivatives session calendar.

Weekends are handled separately. This list is the Exchange capital-market and
F&O holidays for 2026 (circular NSE/CMTR/71775 and later). Cash-only extra
days (e.g. 15 Jan 2026 Maharashtra municipal election) are not included —
stock futures still trade those sessions.
"""

from __future__ import annotations

from datetime import date, datetime

# Weekday F&O holidays, 2026. Weekend festivals (Mahashivratri, Id-Ul-Fitr,
# Independence Day, Diwali Laxmi Pujan / Muhurat) are not listed — Saturday /
# Sunday already skip the scan.
NSE_FO_HOLIDAYS: frozenset[date] = frozenset(
    {
        date(2026, 1, 26),   # Republic Day
        date(2026, 3, 3),    # Holi
        date(2026, 3, 26),   # Shri Ram Navami
        date(2026, 3, 31),   # Shri Mahavir Jayanti
        date(2026, 4, 3),    # Good Friday
        date(2026, 4, 14),   # Dr. Baba Saheb Ambedkar Jayanti
        date(2026, 5, 1),    # Maharashtra Day
        date(2026, 5, 28),   # Bakri Id
        date(2026, 6, 26),   # Muharram
        date(2026, 9, 14),   # Ganesh Chaturthi
        date(2026, 10, 2),   # Mahatma Gandhi Jayanti
        date(2026, 10, 20),  # Dussehra
        date(2026, 11, 10),  # Diwali-Balipratipada
        date(2026, 11, 24),  # Prakash Gurpurb Sri Guru Nanak Dev
        date(2026, 12, 25),  # Christmas
    }
)


def is_nse_fo_holiday(day: date | datetime | None = None) -> bool:
    """True when NSE equity derivatives are closed for a listed holiday."""
    if day is None:
        day = date.today()
    if isinstance(day, datetime):
        day = day.date()
    return day in NSE_FO_HOLIDAYS


def is_nse_fo_session(day: date | datetime | None = None) -> bool:
    """True on a weekday that is not an F&O holiday."""
    if day is None:
        day = date.today()
    if isinstance(day, datetime):
        day = day.date()
    return day.weekday() < 5 and not is_nse_fo_holiday(day)
