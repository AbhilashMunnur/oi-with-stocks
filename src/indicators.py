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


def calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def calculate_supertrend(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    *,
    period: int = 48,
    multiplier: float = 4.5,
) -> tuple[float | None, str | None]:
    """Return (latest Supertrend value, side) where side is 'above' or 'below'.

    side='below' means price is below the Supertrend line (bearish regime).
    side='above' means price is above the Supertrend line (bullish regime).
    """
    if min(len(high), len(low), len(close)) < period + 2:
        return None, None

    atr = calculate_atr(high, low, close, period).to_numpy(dtype=float)
    high_a = high.to_numpy(dtype=float)
    low_a = low.to_numpy(dtype=float)
    close_a = close.to_numpy(dtype=float)
    hl2 = (high_a + low_a) / 2.0
    basic_ub = hl2 + multiplier * atr
    basic_lb = hl2 - multiplier * atr

    n = len(close_a)
    final_ub = np.full(n, np.nan)
    final_lb = np.full(n, np.nan)
    st = np.full(n, np.nan)
    direction = np.full(n, 0, dtype=int)  # 1 = above / bullish, -1 = below / bearish

    for i in range(n):
        if np.isnan(atr[i]):
            continue

        if i == 0 or np.isnan(final_ub[i - 1]):
            final_ub[i] = basic_ub[i]
            final_lb[i] = basic_lb[i]
            st[i] = final_ub[i]
            direction[i] = -1
            continue

        if basic_ub[i] < final_ub[i - 1] or close_a[i - 1] > final_ub[i - 1]:
            final_ub[i] = basic_ub[i]
        else:
            final_ub[i] = final_ub[i - 1]

        if basic_lb[i] > final_lb[i - 1] or close_a[i - 1] < final_lb[i - 1]:
            final_lb[i] = basic_lb[i]
        else:
            final_lb[i] = final_lb[i - 1]

        if direction[i - 1] <= 0:
            if close_a[i] <= final_ub[i]:
                st[i] = final_ub[i]
                direction[i] = -1
            else:
                st[i] = final_lb[i]
                direction[i] = 1
        else:
            if close_a[i] >= final_lb[i]:
                st[i] = final_lb[i]
                direction[i] = 1
            else:
                st[i] = final_ub[i]
                direction[i] = -1

    if np.isnan(st[-1]) or direction[-1] == 0:
        return None, None
    side = "above" if direction[-1] == 1 else "below"
    return float(st[-1]), side
