from __future__ import annotations

from copy import copy
from dataclasses import dataclass, replace

from src.config import SignalType
from src.data.models import OISnapshot, PriceSnapshot, change_pcr_from_legs


def no_short_skip_reason(
    symbol: str,
    blocked: list[str] | set[str] | tuple[str, ...],
    *,
    is_short: bool,
) -> str | None:
    """Laboratory names are longs-only. Pharma is not blocked here."""
    if not is_short or not blocked:
        return None
    blocked_set = {name.upper() for name in blocked}
    if symbol.upper() in blocked_set:
        return "laboratory — longs only"
    return None


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
    # S2 PCR band totals. Telegram prints these on a second line so ΔPCR
    # always matches the Call/Put ΔOI beside it.
    band_call_oi_change: int | None = None
    band_put_oi_change: int | None = None
    lot_size: int = 0
    supertrend: float | None = None
    candle_pattern: str = ""
    # Set when this row is for Telegram only — paper trading must ignore it.
    skip_reason: str | None = None
    # RSI+candle: cash high/low used as the futures stop for both lots.
    stop_price: float | None = None

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


def strikes_around_wall(
    legs_by_strike: dict[float, dict[str, tuple[int, str]]],
    wall: float,
    n_below: int = 1,
    n_above: int = 1,
) -> list[float]:
    """Listed strikes: ``n_below`` below the wall, the wall, ``n_above`` above.

    Uses the option chain's actual strike list, not a rupee step. If the wall
    is not listed, the nearest listed strike is used as the centre.
    """
    strikes = sorted(strike for strike in legs_by_strike if strike > 0)
    if not strikes or wall <= 0:
        return []
    if wall not in strikes:
        wall = min(strikes, key=lambda strike: abs(strike - wall))
    index = strikes.index(wall)
    lo = max(0, index - n_below)
    hi = min(len(strikes), index + n_above + 1)
    return strikes[lo:hi]


def copy_oi_snapshot(oi: OISnapshot) -> OISnapshot:
    """Shallow copy so Scenario 1 can retarget strikes without touching the RSI book."""
    clone = copy(oi)
    clone.call_oi_change = oi.call_oi_change
    clone.put_oi_change = oi.put_oi_change
    clone.band_call_oi_change = oi.band_call_oi_change
    clone.band_put_oi_change = oi.band_put_oi_change
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
    oi.band_call_oi_change = None
    oi.band_put_oi_change = None


def s1_oi_flow_broken(
    direction: str,
    *,
    call_oi_change: int | None,
    put_oi_change: int | None,
) -> bool:
    """S1 OI-only wall death at the entry strike. Price is not used.

    Short: Call ΔOI ≤ 0 (flat counts as unwind) and Put ΔOI > 0.
    Long: Put ΔOI ≤ 0 and Call ΔOI > 0.
    Missing either leg is not a break.
    """
    if call_oi_change is None or put_oi_change is None:
        return False
    side = direction.upper()
    if side == "SHORT":
        return call_oi_change <= 0 and put_oi_change > 0
    if side == "LONG":
        return put_oi_change <= 0 and call_oi_change > 0
    return False


def s1_oi_flow_observed(
    call_oi_change: int | None,
    put_oi_change: int | None,
) -> bool:
    """True when both wall legs have a session ΔOI print."""
    return call_oi_change is not None and put_oi_change is not None


def resistance_is_broken(oi: OISnapshot, ltp: float | None = None) -> bool:
    """15:15 S1 exit: cash through the Call strike *and* OI flow broken."""
    price = ltp if ltp is not None else oi.ltp
    strike = oi.max_call_oi_strike
    if strike <= 0 or price <= strike:
        return False
    return s1_oi_flow_broken(
        "SHORT",
        call_oi_change=oi.call_oi_change,
        put_oi_change=oi.put_oi_change,
    )


def support_is_broken(oi: OISnapshot, ltp: float | None = None) -> bool:
    """15:15 S1 exit: cash through the Put strike *and* OI flow broken."""
    price = ltp if ltp is not None else oi.ltp
    strike = oi.max_put_oi_strike
    if strike <= 0 or price >= strike:
        return False
    return s1_oi_flow_broken(
        "LONG",
        call_oi_change=oi.call_oi_change,
        put_oi_change=oi.put_oi_change,
    )


def s2_invalidation_reason(
    direction: str,
    strike: float,
    cash_ltp: float,
    *,
    call_oi_change: int | None,
    put_oi_change: int | None,
) -> str | None:
    """One scan of S2 OI failure: cash through the entry strike *or*
    writing gone at that strike. Callers must confirm on a second scan
    via ``s2_confirm_invalidation`` before exiting. The 3% futures stop
    is a separate single-scan backup.

    ``strike_through`` wins if both are true. Missing ΔOI does not fire
    ``writing_gone`` (no data ≠ covering).
    """
    if strike <= 0 or cash_ltp <= 0:
        return None
    side = direction.upper()
    if side == "SHORT":
        if cash_ltp > strike:
            return "strike_through"
        if call_oi_change is not None and call_oi_change <= 0:
            return "writing_gone"
        return None
    if side == "LONG":
        if cash_ltp < strike:
            return "strike_through"
        if put_oi_change is not None and put_oi_change <= 0:
            return "writing_gone"
        return None
    return None


def s2_wall_still_valid(
    direction: str,
    strike: float,
    cash_ltp: float,
    *,
    call_oi_change: int | None,
    put_oi_change: int | None,
) -> bool:
    """True only when this scan can see the wall holding: cash not through
    the strike *and* writers still adding on the entry side. Missing ΔOI
    is not a valid wall (and is not an exit).
    """
    if s2_invalidation_reason(
        direction,
        strike,
        cash_ltp,
        call_oi_change=call_oi_change,
        put_oi_change=put_oi_change,
    ):
        return False
    side = direction.upper()
    if side == "SHORT":
        return call_oi_change is not None and call_oi_change > 0
    if side == "LONG":
        return put_oi_change is not None and put_oi_change > 0
    return False


def s2_confirm_invalidation(
    pending: str,
    why: str | None,
    *,
    wall_valid: bool,
) -> tuple[str, bool]:
    """Require two consecutive invalid scans before an OI exit.

    Returns ``(new_pending_reason, should_exit)``. A clearly valid wall
    clears the first flag. Missing data leaves the flag as-is so a gap
    does not count as either confirmation or a reset.
    """
    if why:
        if pending:
            return why, True
        return why, False
    if wall_valid:
        return "", False
    return pending or "", False


def retag_s1_alert_as_s2(alert: ScanAlert) -> ScanAlert:
    """Same uncrossed-wall setup as S1, tagged for the S2 paper book."""
    mapping = {
        SignalType.CALL_OI_S1: SignalType.CALL_OI_S2,
        SignalType.PUT_OI_S1: SignalType.PUT_OI_S2,
    }
    signal = mapping.get(alert.signal, alert.signal)
    message = alert.message.replace(" S1", " S2") if alert.message else alert.message
    return replace(alert, signal=signal, message=message)


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
        band_call = oi.band_call_oi_change
        band_put = oi.band_put_oi_change
    else:
        band_call = band_put = None
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
        band_call_oi_change=band_call,
        band_put_oi_change=band_put,
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
    require_change_pcr: bool = False,
    **_: object,
) -> str | None:
    """Why a CALL_OI short should be skipped, or None if flow supports the short.

    - When require_call_writing: Call ΔOI at the reference (max Call OI) strike
      must be known and > 0 (writing, not unwind/flat).
    - When both legs are writing (wall, or the S2 PCR band if set), Put ΔOI /
      Call ΔOI must be strictly below max_change_pcr.
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
    pcr_name = "band ΔPCR" if oi.band_call_oi_change is not None else "ΔPCR"
    if require_change_pcr and pcr is None:
        return f"{pcr_name} unavailable (need both sides writing in the band)"
    if pcr is not None and pcr >= max_change_pcr:
        return (
            f"{pcr_name} {pcr:.2f} >= {max_change_pcr:g} "
            "(short requires a lower ΔPCR)"
        )

    return None


def put_oi_flow_rejection(
    oi: OISnapshot,
    *,
    require_put_writing: bool = True,
    min_change_pcr: float = 1.0,
    require_change_pcr: bool = False,
    **_: object,
) -> str | None:
    """Why a PUT_OI long should be skipped, or None if flow supports the long.

    Mirror of call_oi_flow_rejection:
    - When require_put_writing: Put ΔOI at the reference (max Put OI) strike
      must be known and > 0 (writing, not unwind/flat).
    - When both legs are writing (wall, or the S2 PCR band if set), Put ΔOI /
      Call ΔOI must be strictly above min_change_pcr.
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
    pcr_name = "band ΔPCR" if oi.band_put_oi_change is not None else "ΔPCR"
    if require_change_pcr and pcr is None:
        return f"{pcr_name} unavailable (need both sides writing in the band)"
    if pcr is not None and pcr <= min_change_pcr:
        return (
            f"{pcr_name} {pcr:.2f} <= {min_change_pcr:g} "
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
    require_change_pcr: bool = False,
) -> ScanAlert | None:
    signal = matched_signal(price, oi, rsi_call_threshold, rsi_put_threshold, proximity_pct)
    if signal is None:
        return None

    flow = dict(
        require_call_writing=require_call_writing,
        max_change_pcr=max_change_pcr,
        require_put_writing=require_put_writing,
        min_change_pcr=min_change_pcr,
        require_change_pcr=require_change_pcr,
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
    wall_pcr = change_pcr_from_legs(oi.call_oi_change, oi.put_oi_change)

    # Name both legs; "ΔOI" alone reads as though puts might be included.
    # Wall ΔPCR is from these two numbers. S2 band PCR is named separately.
    detail = (
        f"OI: {format_oi(oi, value)}, "
        f"Call ΔOI {format_oi_change(oi, oi.call_oi_change)}, "
        f"Put ΔOI {format_oi_change(oi, oi.put_oi_change)}, "
        f"distance: {distance:.2f}%"
    )
    if wall_pcr is not None:
        detail += f", ΔPCR {wall_pcr:.2f}"
    if oi.band_call_oi_change is not None or oi.band_put_oi_change is not None:
        band_pcr = change_pcr_from_legs(oi.band_call_oi_change, oi.band_put_oi_change)
        detail += (
            f", band Call ΔOI {format_oi_change(oi, oi.band_call_oi_change)}, "
            f"band Put ΔOI {format_oi_change(oi, oi.band_put_oi_change)}"
        )
        if band_pcr is not None:
            detail += f", band ΔPCR {band_pcr:.2f}"

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
        change_pcr=oi.change_pcr,
        band_call_oi_change=oi.band_call_oi_change,
        band_put_oi_change=oi.band_put_oi_change,
        lot_size=oi.lot_size,
        message=(
            f"{price.symbol}: {comparison} and price ₹{ltp:.2f} is near max {label} "
            f"OI strike ₹{strike:.0f} ({detail})"
        ),
    )
