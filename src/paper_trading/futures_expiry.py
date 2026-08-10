from __future__ import annotations

import re
from datetime import date

# NSE monthly stock futures symbols look like TITAN26OCTFUT.
# Angel's master also lists day-prefixed aliases (TITAN27OCT26FUT); ignore those.
_STANDARD_FUT = re.compile(
    r"^.+?"
    r"(?P<yy>\d{2})"
    r"(?P<mon>JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)"
    r"FUT$"
)

_MONTH_NUM = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}


def target_futures_year_month(as_of: date, month_index: int = 3) -> tuple[int, int]:
    """Return the calendar month for futures.

    ``month_index`` is 1-based from the present month, so 3 means the far
    month traders call the "3rd month" (August → October).
    """
    if month_index < 1:
        raise ValueError("month_index must be >= 1")

    offset = month_index - 1
    month = as_of.month + offset
    year = as_of.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    return year, month


def is_standard_stock_future(symbol: str) -> bool:
    return bool(_STANDARD_FUT.match(symbol.upper()))


def futures_symbol_year_month(symbol: str) -> tuple[int, int] | None:
    match = _STANDARD_FUT.match(symbol.upper())
    if not match:
        return None
    year = 2000 + int(match.group("yy"))
    return year, _MONTH_NUM[match.group("mon")]
