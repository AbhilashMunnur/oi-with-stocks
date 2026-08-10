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
    # Angel One reports open interest in shares; lot size converts it to contracts.
    lot_size: int = 0

    def contracts(self, open_interest: int) -> int | None:
        if self.lot_size <= 0:
            return None
        return open_interest // self.lot_size
