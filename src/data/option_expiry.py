from __future__ import annotations

from datetime import date, timedelta


def last_thursday(year: int, month: int) -> date:
    """NSE stock F&O monthly expiry (last Thursday of the month)."""
    if month == 12:
        last = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - 3) % 7)


def select_current_month_oi_expiry(
    expiries: list[date], today: date | None = None
) -> date | None:
    """Front-month *monthly* options expiry, not the nearest weekly.

    Uses the last listed expiry in the current calendar month (the monthly
    series). If that month is already done, uses the last expiry of the next
    month that still has contracts.
    """
    today = today or date.today()
    remaining = [expiry for expiry in expiries if expiry >= today]
    if not remaining:
        return None

    by_month: dict[tuple[int, int], list[date]] = {}
    for expiry in remaining:
        by_month.setdefault((expiry.year, expiry.month), []).append(expiry)

    year, month = today.year, today.month
    for _ in range(6):
        bucket = by_month.get((year, month))
        if bucket:
            monthly = last_thursday(year, month)
            if monthly in bucket:
                return monthly
            return max(bucket)
        month += 1
        if month == 13:
            month = 1
            year += 1
    return min(remaining)
