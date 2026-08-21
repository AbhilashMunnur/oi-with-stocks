from __future__ import annotations

import pandas as pd
import yfinance as yf

from src.config import OIConfig, SignalType, SupertrendConfig
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


def _ohlc_from_download(data: pd.DataFrame, yahoo: str) -> tuple[pd.Series, pd.Series, pd.Series] | None:
    if data is None or data.empty:
        return None
    try:
        if isinstance(data.columns, pd.MultiIndex):
            close = data[yahoo]["Close"] if yahoo in data.columns.get_level_values(0) else None
            if close is None:
                return None
            high = data[yahoo]["High"]
            low = data[yahoo]["Low"]
        else:
            close, high, low = data["Close"], data["High"], data["Low"]
    except (KeyError, TypeError):
        return None

    close = close.dropna().astype(float)
    if close.empty:
        return None
    high = high.reindex(close.index).astype(float)
    low = low.reindex(close.index).astype(float)
    return high, low, close


def fetch_supertrends(
    symbols: list[str],
    prices: dict[str, float],
    *,
    atr_period: int,
    multiplier: float,
) -> dict[str, tuple[float, str]]:
    """Daily Supertrend for many NSE symbols via one batched Yahoo download.

    Six months is enough for ATR(20); two years was slowing the 5-minute alert window.
    """
    if not symbols:
        return {}

    yahoo_of = {symbol.upper(): f"{symbol.upper()}.NS" for symbol in symbols}
    bare_of = {yahoo: bare for bare, yahoo in yahoo_of.items()}
    results: dict[str, tuple[float, str]] = {}

    batch_size = 50
    yahoo_list = list(yahoo_of.values())
    for start in range(0, len(yahoo_list), batch_size):
        batch = yahoo_list[start : start + batch_size]
        data = yf.download(
            batch,
            period="6mo",
            interval="1d",
            group_by="ticker",
            threads=True,
            progress=False,
            auto_adjust=False,
        )
        if data is None or data.empty:
            continue

        for yahoo in batch:
            ohlc = _ohlc_from_download(data, yahoo)
            if ohlc is None:
                continue
            high, low, close = ohlc
            bare = bare_of[yahoo]
            ltp = prices.get(bare)
            if ltp:
                close = close.copy()
                high = high.copy()
                low = low.copy()
                close.iloc[-1] = ltp
                high.iloc[-1] = max(float(high.iloc[-1]), ltp)
                low.iloc[-1] = min(float(low.iloc[-1]), ltp)

            st_value, side = calculate_supertrend(
                high, low, close, period=atr_period, multiplier=multiplier
            )
            if st_value is not None and side is not None:
                results[bare] = (st_value, side)

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


def make_supertrend_watch(
    *,
    symbol: str,
    ltp: float,
    supertrend: float,
    side: str,
    oi: OISnapshot | None,
    skip_reason: str | None = None,
) -> ScanAlert:
    """Telegram row for a name near Supertrend (taken or skipped)."""
    signal = SignalType.ST_BEARISH if side == "below" else SignalType.ST_BULLISH
    distance = distance_to_strike_pct(ltp, supertrend) if supertrend else 0.0
    strike = oi.max_call_oi_strike if oi else 0.0
    value = oi.max_call_oi if oi else 0
    return ScanAlert(
        symbol=symbol,
        signal=signal,
        ltp=ltp,
        rsi=0.0,
        oi_strike=strike,
        oi_value=value,
        distance_pct=distance,
        expiry=oi.expiry if oi else "",
        oi_change=oi.call_oi_change if oi and signal is SignalType.ST_BEARISH else (
            oi.put_oi_change if oi else None
        ),
        call_oi_change=oi.call_oi_change if oi else None,
        put_oi_change=oi.put_oi_change if oi else None,
        change_pcr=oi.change_pcr if oi else None,
        lot_size=oi.lot_size if oi else 0,
        supertrend=supertrend,
        skip_reason=skip_reason,
        message=(
            f"{symbol}: ₹{ltp:,.2f} vs ST ₹{supertrend:,.2f} ({distance:.2f}% away)"
            + (f" — not taking ({skip_reason})" if skip_reason else "")
        ),
    )


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
