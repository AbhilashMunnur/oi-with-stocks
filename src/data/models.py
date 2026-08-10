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
    max_call_token: str = ""
    max_put_token: str = ""
    # Change against the previous session's close, filled in on demand.
    call_oi_change: int | None = None
    put_oi_change: int | None = None

    def contracts(self, open_interest: int) -> int | None:
        if self.lot_size <= 0:
            return None
        return int(open_interest / self.lot_size)

    @property
    def change_pcr(self) -> float | None:
        """Put/call ratio of OI *change* at the two peak strikes.

        Only meaningful while both sides are adding positions; if either is
        unwinding the ratio flips sign and stops describing anything useful.
        """
        call_change, put_change = self.call_oi_change, self.put_oi_change
        if not call_change or put_change is None:
            return None
        if call_change <= 0 or put_change <= 0:
            return None
        return put_change / call_change
