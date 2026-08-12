from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import pandas as pd
import yfinance as yf

from src.indicators import calculate_rsi


@dataclass
class UsRsiHit:
    symbol: str
    rsi: float
    close: float
    as_of: str  # YYYY-MM-DD of the last daily bar


def apply_split_adjustment(close: pd.Series, splits: pd.Series) -> pd.Series:
    """Backward-adjust closes for stock splits.

    Yahoo's Adj Close / auto_adjust can lag right after a split (e.g. MNST 2-for-1
    on 2026-08-11), which creates a fake crash and a bogus RSI. Applying the
    split table ourselves keeps the series continuous.
    """
    if close.empty:
        return close

    adjusted = close.astype(float).copy()
    if splits is None or len(splits) == 0:
        return adjusted.dropna()

    index_tz = adjusted.index.tz
    for when, ratio in sorted(splits.items(), reverse=True):
        ratio = float(ratio)
        if ratio == 0:
            continue
        when = pd.Timestamp(when)
        if index_tz is not None:
            when = when.tz_convert(index_tz) if when.tzinfo else when.tz_localize(index_tz)
        else:
            when = when.tz_localize(None) if when.tzinfo else when
        adjusted.loc[adjusted.index < when] = adjusted.loc[adjusted.index < when] / ratio

    return adjusted.dropna()


def _splits_for(symbol: str) -> pd.Series:
    try:
        splits = yf.Ticker(symbol).splits
    except Exception:
        return pd.Series(dtype=float)
    if splits is None:
        return pd.Series(dtype=float)
    return splits


def fetch_daily_closes(symbols: list[str], history_days: int) -> dict[str, pd.Series]:
    """Download daily closes and split-adjust them for RSI."""
    if not symbols:
        return {}

    period = "6mo" if history_days <= 130 else "1y"
    closes: dict[str, pd.Series] = {}

    batch_size = 50
    for start in range(0, len(symbols), batch_size):
        batch = symbols[start : start + batch_size]
        data = yf.download(
            batch,
            period=period,
            group_by="ticker",
            threads=True,
            progress=False,
            auto_adjust=False,
        )
        if data is None or data.empty:
            continue

        raw_closes: dict[str, pd.Series] = {}
        for symbol in batch:
            try:
                if isinstance(data.columns, pd.MultiIndex):
                    series = data[symbol]["Close"]
                else:
                    series = data["Close"]
                raw_closes[symbol] = series
            except (KeyError, TypeError):
                continue

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_splits_for, symbol): symbol for symbol in raw_closes}
            splits_by_symbol = {futures[future]: future.result() for future in as_completed(futures)}

        for symbol, series in raw_closes.items():
            adjusted = apply_split_adjustment(series, splits_by_symbol.get(symbol, pd.Series(dtype=float)))
            if not adjusted.empty:
                closes[symbol] = adjusted

    return closes


def scan_oversold(
    symbols: list[str],
    *,
    rsi_period: int,
    rsi_threshold: float,
    history_days: int,
) -> list[UsRsiHit]:
    """Return symbols whose latest daily RSI is at or below the threshold."""
    closes_by_symbol = fetch_daily_closes(symbols, history_days)
    hits: list[UsRsiHit] = []

    for symbol in symbols:
        series = closes_by_symbol.get(symbol)
        if series is None or len(series) < rsi_period + 1:
            continue

        rsi = calculate_rsi(series, period=rsi_period)
        if rsi is None or rsi > rsi_threshold:
            continue

        last = float(series.iloc[-1])
        as_of = series.index[-1]
        as_of_str = as_of.strftime("%Y-%m-%d") if hasattr(as_of, "strftime") else str(as_of)[:10]
        hits.append(
            UsRsiHit(
                symbol=symbol,
                rsi=float(rsi),
                close=last,
                as_of=as_of_str,
            )
        )

    hits.sort(key=lambda hit: hit.rsi)
    return hits
