from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import pandas as pd
import yfinance as yf

from src.indicators import calculate_rsi


@dataclass
class RsiHit:
    symbol: str
    rsi: float
    close: float
    as_of: str  # YYYY-MM-DD of the last bar


def apply_split_adjustment(close: pd.Series, splits: pd.Series) -> pd.Series:
    """Backward-adjust closes for stock splits.

    Yahoo's Adj Close / auto_adjust can lag right after a split, which creates a
    fake crash and a bogus RSI. Applying the split table ourselves keeps the
    series continuous.
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


def _splits_for(yahoo_symbol: str) -> pd.Series:
    try:
        splits = yf.Ticker(yahoo_symbol).splits
    except Exception:
        return pd.Series(dtype=float)
    if splits is None:
        return pd.Series(dtype=float)
    return splits


def _yahoo_period(history_days: int, interval: str) -> str:
    if interval == "1wk":
        # 14 weekly bars need ~4 months minimum; keep 2y for stable Wilder RSI.
        return "2y"
    return "6mo" if history_days <= 130 else "1y"


def fetch_closes(
    symbols: list[str],
    history_days: int,
    *,
    yahoo_suffix: str = "",
    interval: str = "1d",
) -> dict[str, pd.Series]:
    """Download closes and split-adjust them for RSI.

    `symbols` are bare tickers (e.g. RELIANCE). Pass yahoo_suffix='.NS' for NSE.
    `interval` is a yfinance interval ('1d' or '1wk').
    Returned dict is keyed by the bare ticker.
    """
    if not symbols:
        return {}

    period = _yahoo_period(history_days, interval)
    closes: dict[str, pd.Series] = {}
    yahoo_of = {symbol: f"{symbol}{yahoo_suffix}" for symbol in symbols}
    bare_of = {yahoo: bare for bare, yahoo in yahoo_of.items()}

    batch_size = 50
    yahoo_symbols = [yahoo_of[s] for s in symbols]
    for start in range(0, len(yahoo_symbols), batch_size):
        batch = yahoo_symbols[start : start + batch_size]
        data = yf.download(
            batch,
            period=period,
            interval=interval,
            group_by="ticker",
            threads=True,
            progress=False,
            auto_adjust=False,
        )
        if data is None or data.empty:
            continue

        raw_closes: dict[str, pd.Series] = {}
        for yahoo_symbol in batch:
            try:
                if isinstance(data.columns, pd.MultiIndex):
                    series = data[yahoo_symbol]["Close"]
                else:
                    series = data["Close"]
                raw_closes[yahoo_symbol] = series
            except (KeyError, TypeError):
                continue

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_splits_for, symbol): symbol for symbol in raw_closes}
            splits_by_yahoo = {futures[future]: future.result() for future in as_completed(futures)}

        for yahoo_symbol, series in raw_closes.items():
            adjusted = apply_split_adjustment(
                series, splits_by_yahoo.get(yahoo_symbol, pd.Series(dtype=float))
            )
            if not adjusted.empty:
                closes[bare_of[yahoo_symbol]] = adjusted

    return closes


# Backward-compatible name used by older callers/tests.
def fetch_daily_closes(
    symbols: list[str],
    history_days: int,
    *,
    yahoo_suffix: str = "",
) -> dict[str, pd.Series]:
    return fetch_closes(symbols, history_days, yahoo_suffix=yahoo_suffix, interval="1d")


def scan_oversold(
    symbols: list[str],
    *,
    rsi_period: int,
    rsi_threshold: float,
    history_days: int,
    yahoo_suffix: str = "",
    interval: str = "1d",
) -> list[RsiHit]:
    """Return symbols whose latest RSI is at or below the threshold."""
    closes_by_symbol = fetch_closes(
        symbols,
        history_days,
        yahoo_suffix=yahoo_suffix,
        interval=interval,
    )
    hits: list[RsiHit] = []

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
            RsiHit(
                symbol=symbol,
                rsi=float(rsi),
                close=last,
                as_of=as_of_str,
            )
        )

    hits.sort(key=lambda hit: hit.rsi)
    return hits
