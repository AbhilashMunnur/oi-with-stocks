from __future__ import annotations

from dataclasses import dataclass

from src.config import SignalType
from src.data.nse_client import OISnapshot
from src.data.price_client import PriceSnapshot


@dataclass
class ScanAlert:
    symbol: str
    signal: SignalType
    ltp: float
    rsi: float
    oi_strike: float
    oi_value: int
    distance_pct: float
    expiry: str
    message: str


def is_near_strike(price: float, strike: float, proximity_pct: float) -> bool:
    if strike <= 0:
        return False
    distance_pct = abs(price - strike) / strike * 100
    return distance_pct <= proximity_pct


def distance_to_strike_pct(price: float, strike: float) -> float:
    if strike <= 0:
        return float("inf")
    return abs(price - strike) / strike * 100


def evaluate_stock(
    price: PriceSnapshot,
    oi: OISnapshot,
    rsi_call_threshold: float,
    rsi_put_threshold: float,
    proximity_pct: float,
) -> ScanAlert | None:
    if price.rsi is None:
        return None

    ltp = oi.ltp if oi.ltp > 0 else price.ltp
    rsi = price.rsi

    # RSI >= 70 and price near highest call OI strike
    if rsi >= rsi_call_threshold and is_near_strike(ltp, oi.max_call_oi_strike, proximity_pct):
        distance = distance_to_strike_pct(ltp, oi.max_call_oi_strike)
        return ScanAlert(
            symbol=price.symbol,
            signal=SignalType.CALL_OI,
            ltp=ltp,
            rsi=rsi,
            oi_strike=oi.max_call_oi_strike,
            oi_value=oi.max_call_oi,
            distance_pct=distance,
            expiry=oi.expiry,
            message=(
                f"{price.symbol}: RSI {rsi:.1f} (>= {rsi_call_threshold}) and price "
                f"₹{ltp:.2f} is near max Call OI strike ₹{oi.max_call_oi_strike:.0f} "
                f"(OI: {oi.max_call_oi:,}, distance: {distance:.2f}%)"
            ),
        )

    # RSI <= 35 and price near highest put OI strike
    if rsi <= rsi_put_threshold and is_near_strike(ltp, oi.max_put_oi_strike, proximity_pct):
        distance = distance_to_strike_pct(ltp, oi.max_put_oi_strike)
        return ScanAlert(
            symbol=price.symbol,
            signal=SignalType.PUT_OI,
            ltp=ltp,
            rsi=rsi,
            oi_strike=oi.max_put_oi_strike,
            oi_value=oi.max_put_oi,
            distance_pct=distance,
            expiry=oi.expiry,
            message=(
                f"{price.symbol}: RSI {rsi:.1f} (<= {rsi_put_threshold}) and price "
                f"₹{ltp:.2f} is near max Put OI strike ₹{oi.max_put_oi_strike:.0f} "
                f"(OI: {oi.max_put_oi:,}, distance: {distance:.2f}%)"
            ),
        )

    return None
