from __future__ import annotations

from copy import copy
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
    # Set when this row is for Telegram only — paper trading must ignore it.
    skip_reason: str | None = None

    def in_contracts(self, shares: int | None) -> int | None:
        """Angel One reports OI in shares; traders read it in contracts."""
        if shares is None or self.lot_size <= 0:
            return None
        return int(shares / self.lot_size)


def select_active_oi_walls(
    legs_by_strike: dict[float, dict[str, tuple[int, str]]],
    ltp: float,
) -> tuple[tuple[float, int, str] | None, tuple[float, int, str] | None]:
    """Highest Call OI at/above price (resistance) and Put OI at/below price (support).

    Broken walls are ignored: Call OI below spot is not resistance, Put OI above
    spot is not support. Returns ``(call_wall, put_wall)`` where each wall is
    ``(strike, oi_shares, token)`` or None.
    """
    call_best: tuple[int, float, str] | None = None  # oi, strike, token
    put_best: tuple[int, float, str] | None = None

    for strike, legs in legs_by_strike.items():
        if strike <= 0:
            continue
        call_ok = put_ok = True
        if ltp > 0:
            call_ok = strike >= ltp
            put_ok = strike <= ltp

        if call_ok and "CE" in legs:
            oi_shares, token = legs["CE"]
            if call_best is None or oi_shares > call_best[0]:
                call_best = (oi_shares, strike, token)

        if put_ok and "PE" in legs:
            oi_shares, token = legs["PE"]
            if put_best is None or oi_shares > put_best[0]:
                put_best = (oi_shares, strike, token)

    call_wall = (call_best[1], call_best[0], call_best[2]) if call_best else None
    put_wall = (put_best[1], put_best[0], put_best[2]) if put_best else None
    return call_wall, put_wall


def is_substantial_fallback_wall(
    fallback: tuple[float, int, str] | None,
    peak: tuple[float, int, str] | None,
    min_pct: float = 50.0,
) -> bool:
    """True if `fallback` is the peak, or a real wall vs the peak's OI."""
    if fallback is None:
        return False
    if peak is None or peak[1] <= 0 or fallback[0] == peak[0]:
        return True
    return (fallback[1] / peak[1]) * 100 >= min_pct


def choose_s1_entry_wall(
    legs_by_strike: dict[float, dict[str, tuple[int, str]]],
    ltp: float,
    side: str,
    min_fallback_oi_pct: float = 50.0,
) -> tuple[float, int, str] | None:
    """S1 entry: highest uncrossed wall only.

    A peak already through price is never the entry, even if writing continues.
    If the uncrossed wall is not the peak, it must hold at least
    ``min_fallback_oi_pct`` of the peak's OI.
    """
    peak_call, peak_put = select_active_oi_walls(legs_by_strike, ltp=0)
    active_call, active_put = select_active_oi_walls(legs_by_strike, ltp)
    if side == "call":
        wall, peak = active_call, peak_call
    else:
        wall, peak = active_put, peak_put
    if not is_substantial_fallback_wall(wall, peak, min_fallback_oi_pct):
        return None
    return wall


def copy_oi_snapshot(oi: OISnapshot) -> OISnapshot:
    """Shallow copy so Scenario 1 can retarget strikes without touching the RSI book."""
    clone = copy(oi)
    clone.call_oi_change = oi.call_oi_change
    clone.put_oi_change = oi.put_oi_change
    return clone


def apply_oi_wall(oi: OISnapshot, wall: tuple[float, int, str], side: str) -> None:
    strike, shares, token = wall
    if side == "call":
        oi.max_call_oi_strike = strike
        oi.max_call_oi = shares
        oi.max_call_token = token
    else:
        oi.max_put_oi_strike = strike
        oi.max_put_oi = shares
        oi.max_put_token = token
    oi.call_oi_change = None
    oi.put_oi_change = None


def resistance_is_broken(oi: OISnapshot, ltp: float | None = None) -> bool:
    """Bearish setup: resistance is broken only if price has crossed above the
    Call strike *and* calls unwind while puts are added there.
    """
    price = ltp if ltp is not None else oi.ltp
    strike = oi.max_call_oi_strike
    if strike <= 0 or price <= strike:
        return False
    if oi.call_oi_change is None or oi.put_oi_change is None:
        return False
    return oi.call_oi_change <= 0 and oi.put_oi_change > 0


def support_is_broken(oi: OISnapshot, ltp: float | None = None) -> bool:
    """Bullish setup: support is broken only if price has crossed below the
    Put strike *and* puts unwind while calls are added there.
    """
    price = ltp if ltp is not None else oi.ltp
    strike = oi.max_put_oi_strike
    if strike <= 0 or price >= strike:
        return False
    if oi.call_oi_change is None or oi.put_oi_change is None:
        return False
    return oi.put_oi_change <= 0 and oi.call_oi_change > 0


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


def rsi_watch_side(
    price: PriceSnapshot,
    rsi_call_threshold: float,
    rsi_put_threshold: float,
) -> SignalType | None:
    """CALL_OI if RSI is stretched high, PUT_OI if stretched low."""
    if price.rsi is None:
        return None
    if price.rsi >= rsi_call_threshold:
        return SignalType.CALL_OI
    if price.rsi <= rsi_put_threshold:
        return SignalType.PUT_OI
    return None


def proximity_skip_reason(
    price: PriceSnapshot,
    oi: OISnapshot,
    signal: SignalType,
    proximity_pct: float,
) -> str | None:
    """Why this name is not near its wall, or None if it is within proximity."""
    strike = (
        oi.max_call_oi_strike if signal is SignalType.CALL_OI else oi.max_put_oi_strike
    )
    label = "Call" if signal is SignalType.CALL_OI else "Put"
    if strike <= 0:
        side = "at/above" if signal is SignalType.CALL_OI else "at/below"
        return f"no {label} OI wall {side} price"
    ltp = price.ltp if price.ltp > 0 else oi.ltp
    if is_near_strike(ltp, strike, proximity_pct):
        return None
    distance = distance_to_strike_pct(ltp, strike)
    return f"{distance:.2f}% from max {label} OI (need ≤ {proximity_pct:g}%)"


def make_rsi_alert(
    price: PriceSnapshot,
    oi: OISnapshot | None,
    signal: SignalType,
    skip_reason: str | None = None,
) -> ScanAlert:
    """Watchlist row for an RSI-stretched name (taken or skipped)."""
    ltp = price.ltp
    strike = 0.0
    value = 0
    expiry = ""
    lot_size = 0
    call_change = put_change = pcr = None
    if oi:
        if ltp <= 0:
            ltp = oi.ltp
        strike = (
            oi.max_call_oi_strike if signal is SignalType.CALL_OI else oi.max_put_oi_strike
        )
        value = oi.max_call_oi if signal is SignalType.CALL_OI else oi.max_put_oi
        expiry = oi.expiry
        lot_size = oi.lot_size
        call_change = oi.call_oi_change
        put_change = oi.put_oi_change
        pcr = oi.change_pcr
    distance = distance_to_strike_pct(ltp, strike) if strike > 0 else 0.0
    label = "Call" if signal is SignalType.CALL_OI else "Put"
    status = f"not taking ({skip_reason})" if skip_reason else "taking position"
    return ScanAlert(
        symbol=price.symbol,
        signal=signal,
        ltp=ltp,
        rsi=price.rsi or 0.0,
        oi_strike=strike,
        oi_value=value,
        distance_pct=distance,
        expiry=expiry,
        oi_change=call_change if signal is SignalType.CALL_OI else put_change,
        call_oi_change=call_change,
        put_oi_change=put_change,
        change_pcr=pcr,
        lot_size=lot_size,
        skip_reason=skip_reason,
        message=(
            f"{price.symbol}: RSI {price.rsi:.1f} vs max {label} OI "
            f"₹{strike:.0f} — {status}"
        ),
    )


def call_oi_flow_rejection(
    oi: OISnapshot,
    *,
    require_call_writing: bool = True,
    max_change_pcr: float = 0.75,
    **_: object,
) -> str | None:
    """Why a CALL_OI short should be skipped, or None if flow supports the short.

    - When require_call_writing: Call ΔOI at the reference (max Call OI) strike
      must be known and > 0 (writing, not unwind/flat).
    - When both legs are writing at that same strike, Put ΔOI / Call ΔOI must be
      strictly below max_change_pcr.
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
    if pcr is not None and pcr >= max_change_pcr:
        return (
            f"ΔPCR {pcr:.2f} >= {max_change_pcr:g} "
            "(short requires a lower ΔPCR)"
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
      strictly above min_change_pcr.
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
    if pcr is not None and pcr <= min_change_pcr:
        return (
            f"ΔPCR {pcr:.2f} <= {min_change_pcr:g} "
            "(long requires a higher ΔPCR)"
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
    max_change_pcr: float = 0.75,
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
