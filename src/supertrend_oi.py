from __future__ import annotations

from datetime import date

import pandas as pd

from src.config import OIConfig, SignalType, SupertrendConfig
from src.data.angelone_client import AngelOneClient
from src.data.models import OISnapshot
from src.indicators import calculate_supertrend
from src.oi_analyzer import (
    ScanAlert,
    call_oi_flow_rejection,
    distance_to_strike_pct,
    format_oi,
    format_oi_change,
    put_oi_flow_rejection,
)


def fetch_supertrends(
    client: AngelOneClient,
    symbols: list[str],
    prices: dict[str, float],
    *,
    atr_period: int,
    multiplier: float,
) -> dict[str, tuple[float, str]]:
    """Daily Supertrend from Angel One OHLC (same candle feed as RSI)."""
    if not symbols:
        return {}

    results: dict[str, tuple[float, str]] = {}
    today = f"{date.today():%Y-%m-%d}"

    for index, symbol in enumerate(symbols, 1):
        rows = client.daily_ohlc(symbol)
        if len(rows) < atr_period + 2:
            continue

        dates = [d for d, _h, _l, _c in rows]
        high = pd.Series([h for _d, h, _l, _c in rows], dtype=float)
        low = pd.Series([low_v for _d, _h, low_v, _c in rows], dtype=float)
        close = pd.Series([c for _d, _h, _l, c in rows], dtype=float)

        ltp = prices.get(symbol)
        if ltp:
            if dates[-1] == today:
                close.iloc[-1] = ltp
                high.iloc[-1] = max(float(high.iloc[-1]), ltp)
                low.iloc[-1] = min(float(low.iloc[-1]), ltp)
            else:
                # Still-forming session not in candle history yet.
                high = pd.concat([high, pd.Series([ltp], dtype=float)], ignore_index=True)
                low = pd.concat([low, pd.Series([ltp], dtype=float)], ignore_index=True)
                close = pd.concat([close, pd.Series([ltp], dtype=float)], ignore_index=True)

        st_value, side = calculate_supertrend(
            high, low, close, period=atr_period, multiplier=multiplier
        )
        if st_value is not None and side is not None:
            results[symbol] = (st_value, side)

        if index % 25 == 0:
            print(f"  Supertrend OHLC {index}/{len(symbols)}...")
            client._save_ohlc_cache()
            client._save_closes_cache()

    client._save_ohlc_cache()
    client._save_closes_cache()
    return results


def near_supertrend_from_below(ltp: float, st: float, proximity_pct: float) -> bool:
    """Price is below ST and within proximity_pct of the line."""
    if st <= 0 or ltp <= 0 or ltp >= st:
        return False
    return distance_to_strike_pct(ltp, st) <= proximity_pct


def near_supertrend_from_above(ltp: float, st: float, proximity_pct: float) -> bool:
    """Price is above ST and within proximity_pct of the line."""
    if st <= 0 or ltp <= 0 or ltp <= st:
        return False
    return distance_to_strike_pct(ltp, st) <= proximity_pct


def evaluate_supertrend_oi(
    *,
    symbol: str,
    ltp: float,
    supertrend: float,
    side: str,
    oi: OISnapshot,
    st_config: SupertrendConfig,
    oi_config: OIConfig,
) -> ScanAlert | None:
    """Build a Supertrend + strike-OI alert, or None if rules are not met.

    Bearish: below ST, near line, bearish ΔOI (call writing / put not dominant) → short.
    Bullish: above ST, near line, bullish ΔOI (put writing / call not dominant) → long.
    """
    distance = distance_to_strike_pct(ltp, supertrend)

    if side == "below" and near_supertrend_from_below(ltp, supertrend, st_config.proximity_pct):
        if call_oi_flow_rejection(
            oi,
            require_call_writing=oi_config.require_call_writing,
            max_change_pcr=oi_config.max_change_pcr,
        ):
            return None
        signal = SignalType.ST_BEARISH
        label = "below Supertrend (resistance)"
    elif side == "above" and near_supertrend_from_above(ltp, supertrend, st_config.proximity_pct):
        if put_oi_flow_rejection(
            oi,
            require_put_writing=oi_config.require_put_writing,
            min_change_pcr=oi_config.min_change_pcr,
        ):
            return None
        signal = SignalType.ST_BULLISH
        label = "above Supertrend (support)"
    else:
        return None

    strike = oi.max_call_oi_strike
    detail = (
        f"ST ₹{supertrend:,.2f}, strike ₹{strike:,.0f}, "
        f"Call ΔOI {format_oi_change(oi, oi.call_oi_change)}, "
        f"Put ΔOI {format_oi_change(oi, oi.put_oi_change)}"
    )
    if oi.change_pcr is not None:
        detail += f", ΔPCR {oi.change_pcr:.2f}"

    return ScanAlert(
        symbol=symbol,
        signal=signal,
        ltp=ltp,
        rsi=0.0,
        oi_strike=strike,
        oi_value=oi.max_call_oi,
        distance_pct=distance,
        expiry=oi.expiry,
        oi_change=oi.call_oi_change if signal is SignalType.ST_BEARISH else oi.put_oi_change,
        call_oi_change=oi.call_oi_change,
        put_oi_change=oi.put_oi_change,
        change_pcr=oi.change_pcr,
        lot_size=oi.lot_size,
        supertrend=supertrend,
        message=(
            f"{symbol}: price ₹{ltp:,.2f} is {label} ({distance:.2f}% away); {detail}; "
            f"OI {format_oi(oi, oi.max_call_oi)}"
        ),
    )
