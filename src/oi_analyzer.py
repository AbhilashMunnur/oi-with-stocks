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
    call_oi_change: int | None = None
    put_oi_change: int | None = None
    change_pcr: float | None = None
    lot_size: int = 0
    supertrend: float | None = None

    def in_contracts(self, shares: int | None) -> int | None:
        """Angel One reports OI in shares; traders read it in contracts."""
        if shares is None or self.lot_size <= 0:
            return None
        return int(shares / self.lot_size)


def align_snapshot_to_reference_strike(
    oi: OISnapshot,
    reference: str,
) -> bool:
    """Point CE and PE tokens (and current OI) at one shared reference strike.

    - ``"call"`` → max Call OI strike (RSI overbought shorts)
    - ``"put"``  → max Put OI strike (RSI oversold longs)

    Both Call ΔOI and Put ΔOI are then computed at that same strike.
    Returns False if the primary leg (CE for call, PE for put) is missing.
    """
    if reference == "call":
        strike = oi.max_call_oi_strike
        if strike <= 0:
            return False
        legs = oi.legs_by_strike.get(strike) or {}
        if "CE" not in legs:
            return False
        oi.max_call_oi, oi.max_call_token = legs["CE"]
        if "PE" in legs:
            oi.max_put_oi, oi.max_put_token = legs["PE"]
        else:
            oi.max_put_token = ""
            oi.put_oi_change = None
        oi.max_put_oi_strike = strike
        return True

    if reference == "put":
        strike = oi.max_put_oi_strike
        if strike <= 0:
            return False
        legs = oi.legs_by_strike.get(strike) or {}
        if "PE" not in legs:
            return False
        oi.max_put_oi, oi.max_put_token = legs["PE"]
        if "CE" in legs:
            oi.max_call_oi, oi.max_call_token = legs["CE"]
        else:
            oi.max_call_token = ""
            oi.call_oi_change = None
        oi.max_call_oi_strike = strike
        return True

    return False


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
    """Signed OI change, labelled so it is clear which leg it belongs to."""
    if change is None:
        return "n/a"
    lots = oi.contracts(change)
    return f"{lots:+,} contracts" if lots is not None else f"{change:+,} shares"


def matched_signal(
    price: PriceSnapshot,
    oi: OISnapshot,
    rsi_call_threshold: float,
    rsi_put_threshold: float,
    proximity_pct: float,
) -> SignalType | None:
    """Which alert, if any, this stock qualifies for on RSI + strike proximity.

    OI-flow filters (call writing / change PCR) are applied separately once
    session ΔOI has been loaded onto `oi`.
    """
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


def call_oi_flow_rejection(
    oi: OISnapshot,
    *,
    require_call_writing: bool = True,
    max_change_pcr: float = 1.0,
    **_: object,
) -> str | None:
    """Why a CALL_OI short should be skipped, or None if flow supports the short.

    - When require_call_writing: Call ΔOI at the reference (max Call OI) strike
      must be known and > 0 (writing, not unwind/flat).
    - When both legs are writing at that same strike, Put ΔOI / Call ΔOI must not
      exceed max_change_pcr (put writing must not dominate call writing).
    """
    if require_call_writing:
        if oi.call_oi_change is None:
            return "Call ΔOI unavailable (need call writing to short)"

        if oi.call_oi_change <= 0:
            return (
                f"Call ΔOI {oi.call_oi_change:+,} shares "
                "(call unwinding/flat — skip short)"
            )

    pcr = oi.change_pcr
    if pcr is not None and pcr > max_change_pcr:
        return (
            f"ΔPCR {pcr:.2f} > {max_change_pcr:g} "
            "(put writing dominates call writing — skip short)"
        )

    return None


def put_oi_flow_rejection(
    oi: OISnapshot,
    *,
    require_put_writing: bool = True,
    min_change_pcr: float = 1.0,
    **_: object,
) -> str | None:
    """Why a PUT_OI long should be skipped, or None if flow supports the long.

    Mirror of call_oi_flow_rejection:
    - When require_put_writing: Put ΔOI at the reference (max Put OI) strike
      must be known and > 0 (writing, not unwind/flat).
    - When both legs are writing at that same strike, Put ΔOI / Call ΔOI must be
      at least min_change_pcr (call writing must not dominate put writing).
    """
    if require_put_writing:
        if oi.put_oi_change is None:
            return "Put ΔOI unavailable (need put writing to long)"

        if oi.put_oi_change <= 0:
            return (
                f"Put ΔOI {oi.put_oi_change:+,} shares "
                "(put unwinding/flat — skip long)"
            )

    pcr = oi.change_pcr
    if pcr is not None and pcr < min_change_pcr:
        return (
            f"ΔPCR {pcr:.2f} < {min_change_pcr:g} "
            "(call writing dominates put writing — skip long)"
        )

    return None


def evaluate_stock(
    price: PriceSnapshot,
    oi: OISnapshot,
    rsi_call_threshold: float,
    rsi_put_threshold: float,
    proximity_pct: float,
    *,
    require_call_writing: bool = True,
    max_change_pcr: float = 1.0,
    require_put_writing: bool = True,
    min_change_pcr: float = 1.0,
) -> ScanAlert | None:
    signal = matched_signal(price, oi, rsi_call_threshold, rsi_put_threshold, proximity_pct)
    if signal is None:
        return None

    flow = dict(
        require_call_writing=require_call_writing,
        max_change_pcr=max_change_pcr,
        require_put_writing=require_put_writing,
        min_change_pcr=min_change_pcr,
    )
    if signal is SignalType.CALL_OI:
        if call_oi_flow_rejection(oi, **flow):
            return None
    else:
        if put_oi_flow_rejection(oi, **flow):
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

    # Name both legs; "ΔOI" alone reads as though puts might be included.
    detail = (
        f"OI: {format_oi(oi, value)}, "
        f"Call ΔOI {format_oi_change(oi, oi.call_oi_change)}, "
        f"Put ΔOI {format_oi_change(oi, oi.put_oi_change)}, "
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
        call_oi_change=oi.call_oi_change,
        put_oi_change=oi.put_oi_change,
        change_pcr=pcr,
        lot_size=oi.lot_size,
        message=(
            f"{price.symbol}: {comparison} and price ₹{ltp:.2f} is near max {label} "
            f"OI strike ₹{strike:.0f} ({detail})"
        ),
    )
