"""Daily US Nasdaq RSI ≤ threshold scanner (post cash close)."""

from __future__ import annotations

from src.daily_rsi import (
    DailyRsiConfig,
    RsiHit,
    apply_split_adjustment,
    format_rsi_digest,
    load_daily_rsi_config,
    load_watchlist,
    scan_oversold,
)

# Backward-compatible aliases used by us_rsi_main / older imports.
UsRsiConfig = DailyRsiConfig
UsRsiHit = RsiHit


def load_us_rsi_config(path: str = "config_us_rsi.yaml") -> DailyRsiConfig:
    return load_daily_rsi_config(path, "us_rsi")


def format_us_rsi_digest(hits: list[RsiHit], *, threshold: float) -> str:
    return format_rsi_digest(
        hits,
        threshold=threshold,
        market_label="US Nasdaq-100",
        currency_symbol="$",
    )


__all__ = [
    "UsRsiConfig",
    "UsRsiHit",
    "apply_split_adjustment",
    "format_us_rsi_digest",
    "load_us_rsi_config",
    "load_watchlist",
    "scan_oversold",
]
