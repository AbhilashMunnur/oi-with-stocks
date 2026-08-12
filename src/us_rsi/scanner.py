from __future__ import annotations

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


def fetch_daily_closes(symbols: list[str], history_days: int) -> dict[str, pd.Series]:
    """Download adjusted daily closes for many symbols in a few batched requests."""
    if not symbols:
        return {}

    # yfinance period strings; 6mo covers ~120 trading days with margin.
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
            auto_adjust=True,
        )
        if data is None or data.empty:
            continue

        if len(batch) == 1:
            symbol = batch[0]
            series = data["Close"].dropna()
            if not series.empty:
                closes[symbol] = series
            continue

        for symbol in batch:
            try:
                series = data[symbol]["Close"].dropna()
            except (KeyError, TypeError):
                continue
            if not series.empty:
                closes[symbol] = series

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

        last = series.iloc[-1]
        as_of = series.index[-1]
        as_of_str = as_of.strftime("%Y-%m-%d") if hasattr(as_of, "strftime") else str(as_of)[:10]
        hits.append(
            UsRsiHit(
                symbol=symbol,
                rsi=float(rsi),
                close=float(last),
                as_of=as_of_str,
            )
        )

    hits.sort(key=lambda hit: hit.rsi)
    return hits
