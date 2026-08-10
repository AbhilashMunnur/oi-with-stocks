from __future__ import annotations

import numpy as np
import pandas as pd


def calculate_rsi(closes: pd.Series, period: int = 14) -> float | None:
    """Return the latest RSI value for a close-price series."""
    if closes is None or len(closes) < period + 1:
        return None

    delta = closes.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)

    avg_gain = gains.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = losses.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    latest = rsi.iloc[-1]

    if pd.isna(latest):
        return None
    return float(latest)
