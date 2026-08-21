from __future__ import annotations

from datetime import date, timedelta

# NSE Nifty weekly (and monthly) index options expire on Tuesday.
NIFTY_EXPIRY_WEEKDAY = 1


def last_thursday(year: int, month: int) -> date:
    """NSE stock F&O monthly expiry (last Thursday of the month)."""
    if month == 12:
        last = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - 3) % 7)


def first_of_next_month(today: date) -> date:
    if today.month == 12:
        return date(today.year + 1, 1, 1)
    return date(today.year, today.month + 1, 1)


def is_nifty_expiry_day(today: date | None = None) -> bool:
    today = today or date.today()
    return today.weekday() == NIFTY_EXPIRY_WEEKDAY


def is_stock_monthly_expiry_day(today: date | None = None) -> bool:
    today = today or date.today()
    return today == last_thursday(today.year, today.month)


def oi_uses_next_month(today: date | None = None) -> bool:
    """Front-month stock OI is noise on Nifty Tuesday and stock monthly expiry."""
    today = today or date.today()
    return is_nifty_expiry_day(today) or is_stock_monthly_expiry_day(today)


def oi_scan_reason(today: date | None = None) -> str:
    today = today or date.today()
    if is_nifty_expiry_day(today):
        return (
            "next-month stock OI (Nifty expiry Tuesday — "
            "front-month unwind is ignored)"
        )
    if is_stock_monthly_expiry_day(today):
        return "next-month stock OI (stock F&O monthly expiry)"
    return "current-month stock OI"


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


def select_scan_oi_expiry(
    expiries: list[date], today: date | None = None
) -> date | None:
    """OI expiry the scanner should read: next month on index/stock expiry days."""
    today = today or date.today()
    chosen = None
    if oi_uses_next_month(today):
        chosen = select_current_month_oi_expiry(
            expiries, first_of_next_month(today)
        )
    return chosen or select_current_month_oi_expiry(expiries, today)
