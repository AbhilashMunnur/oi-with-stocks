from __future__ import annotations

import numpy as np
import pandas as pd


def calculate_smma_series(closes: pd.Series, period: int) -> pd.Series | None:
    """TradingView SMMA / Wilder RMA seeded with SMA of the first `period` closes."""
    if closes is None or period <= 0 or len(closes) < period:
        return None
    values = closes.to_numpy(dtype=float, copy=True)
    out = np.full(len(values), np.nan)
    out[period - 1] = float(np.mean(values[:period]))
    for i in range(period, len(values)):
        out[i] = (out[i - 1] * (period - 1) + values[i]) / period
    return pd.Series(out, index=closes.index)


def calculate_smma(closes: pd.Series, period: int) -> float | None:
    """Return the latest SMMA value for a close-price series."""
    series = calculate_smma_series(closes, period)
    if series is None or series.empty:
        return None
    latest = series.iloc[-1]
    if pd.isna(latest):
        return None
    return float(latest)


def calculate_rsi(closes: pd.Series, period: int = 14) -> float | None:
    """Return the latest RSI value for a close-price series."""
    series = calculate_rsi_series(closes, period)
    if series is None or series.empty:
        return None
    latest = series.iloc[-1]
    if pd.isna(latest):
        return None
    return float(latest)


def calculate_rsi_series(closes: pd.Series, period: int = 14) -> pd.Series | None:
    """Wilder RSI for each bar. Used so yesterday's RSI is not mixed with today's LTP."""
    if closes is None or len(closes) < period + 1:
        return None

    delta = closes.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)

    avg_gain = gains.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = losses.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))
