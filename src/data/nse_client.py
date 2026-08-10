from __future__ import annotations

from dataclasses import dataclass

from nsefetch import MarketService


@dataclass
class OISnapshot:
    symbol: str
    ltp: float
    max_call_oi_strike: float
    max_call_oi: int
    max_put_oi_strike: float
    max_put_oi: int
    expiry: str


class NSEClient:
    def __init__(self):
        self._service = MarketService()

    def close(self) -> None:
        self._service.close()

    def __enter__(self) -> NSEClient:
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def get_oi_snapshot(self, symbol: str) -> OISnapshot | None:
        try:
            result = self._service.get_option_chain(symbol.upper())
        except Exception as exc:
            print(f"  {symbol}: NSE fetch error - {exc}")
            return None

        if not result.success or not result.data:
            return None

        chain = result.data
        ltp = float(chain.underlying_price)
        if ltp <= 0:
            return None

        expiry_dates = chain.expiry_dates or []
        if not expiry_dates:
            return None
        nearest_expiry = expiry_dates[0]

        max_call_oi = -1
        max_call_strike = 0.0
        max_put_oi = -1
        max_put_strike = 0.0

        for entry in chain.entries:
            if entry.expiry_date != nearest_expiry:
                continue

            strike = float(entry.strike_price)
            oi = int(entry.open_interest or 0)

            if entry.option_type == "CE" and oi > max_call_oi:
                max_call_oi = oi
                max_call_strike = strike
            elif entry.option_type == "PE" and oi > max_put_oi:
                max_put_oi = oi
                max_put_strike = strike

        if max_call_oi < 0 or max_put_oi < 0:
            return None

        return OISnapshot(
            symbol=symbol.upper(),
            ltp=ltp,
            max_call_oi_strike=max_call_strike,
            max_call_oi=max_call_oi,
            max_put_oi_strike=max_put_strike,
            max_put_oi=max_put_oi,
            expiry=nearest_expiry,
        )
