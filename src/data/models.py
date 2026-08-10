from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PriceSnapshot:
    symbol: str
    ltp: float
    rsi: float | None


@dataclass
class OISnapshot:
    symbol: str
    ltp: float
    max_call_oi_strike: float
    max_call_oi: int
    max_put_oi_strike: float
    max_put_oi: int
    expiry: str
