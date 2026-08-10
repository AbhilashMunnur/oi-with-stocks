from __future__ import annotations

from dataclasses import dataclass

from src.config import SignalType
from src.data.models import OISnapshot, PriceSnapshot


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
    oi_change: int | None = None
    change_pcr: float | None = None
    buildup: str = ""


def is_near_strike(price: float, strike: float, proximity_pct: float) -> bool:
    if strike <= 0:
        return False
    return distance_to_strike_pct(price, strike) <= proximity_pct


def distance_to_strike_pct(price: float, strike: float) -> float:
    if strike <= 0:
        return float("inf")
    return abs(price - strike) / strike * 100


def format_oi(oi: OISnapshot, open_interest: int) -> str:
    """Render open interest as contracts when the lot size is known."""
    lots = oi.contracts(open_interest)
    if lots is None:
        return f"{open_interest:,} shares"
    return f"{lots:,} contracts"


def format_oi_change(oi: OISnapshot, change: int | None) -> str:
    if change is None:
        return "ΔOI n/a"
    lots = oi.contracts(change)
    value = f"{lots:+,} contracts" if lots is not None else f"{change:+,} shares"
    return f"ΔOI {value}"


def matched_signal(
    price: PriceSnapshot,
    oi: OISnapshot,
    rsi_call_threshold: float,
    rsi_put_threshold: float,
    proximity_pct: float,
) -> SignalType | None:
    """Which alert, if any, this stock qualifies for. No API calls."""
    if price.rsi is None:
        return None

    ltp = price.ltp if price.ltp > 0 else oi.ltp

    if price.rsi >= rsi_call_threshold and is_near_strike(
        ltp, oi.max_call_oi_strike, proximity_pct
    ):
        return SignalType.CALL_OI

    if price.rsi <= rsi_put_threshold and is_near_strike(
        ltp, oi.max_put_oi_strike, proximity_pct
    ):
        return SignalType.PUT_OI

    return None


def evaluate_stock(
    price: PriceSnapshot,
    oi: OISnapshot,
    rsi_call_threshold: float,
    rsi_put_threshold: float,
    proximity_pct: float,
) -> ScanAlert | None:
    signal = matched_signal(price, oi, rsi_call_threshold, rsi_put_threshold, proximity_pct)
    if signal is None:
        return None

    ltp = price.ltp if price.ltp > 0 else oi.ltp
    rsi = price.rsi

    if signal is SignalType.CALL_OI:
        strike, value = oi.max_call_oi_strike, oi.max_call_oi
        change = oi.call_oi_change
        comparison = f"RSI {rsi:.1f} (>= {rsi_call_threshold})"
        label = "Call"
    else:
        strike, value = oi.max_put_oi_strike, oi.max_put_oi
        change = oi.put_oi_change
        comparison = f"RSI {rsi:.1f} (<= {rsi_put_threshold})"
        label = "Put"

    distance = distance_to_strike_pct(ltp, strike)
    pcr = oi.change_pcr

    detail = (
        f"OI: {format_oi(oi, value)}, {format_oi_change(oi, change)}, "
        f"distance: {distance:.2f}%"
    )
    if pcr is not None:
        detail += f", change PCR: {pcr:.2f}"

    return ScanAlert(
        symbol=price.symbol,
        signal=signal,
        ltp=ltp,
        rsi=rsi,
        oi_strike=strike,
        oi_value=value,
        distance_pct=distance,
        expiry=oi.expiry,
        oi_change=change,
        change_pcr=pcr,
        buildup=oi.buildup,
        message=(
            f"{price.symbol}: {comparison} and price ₹{ltp:.2f} is near max {label} "
            f"OI strike ₹{strike:.0f} ({detail})"
        ),
    )
