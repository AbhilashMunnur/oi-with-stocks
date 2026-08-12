from datetime import datetime

import pandas as pd

from src.us_rsi.digest import format_us_rsi_digest
from src.us_rsi.scanner import UsRsiHit, apply_split_adjustment, scan_oversold


def test_format_digest_lists_hits_lowest_rsi_first():
    hits = [
        UsRsiHit("AAA", 28.0, 10.0, "2026-08-11"),
        UsRsiHit("BBB", 31.5, 20.0, "2026-08-11"),
    ]
    text = format_us_rsi_digest(hits, threshold=32)
    assert "RSI ≤ 32" in text
    assert "AAA" in text and "BBB" in text
    assert text.index("AAA") < text.index("BBB")


def test_format_digest_when_empty():
    text = format_us_rsi_digest([], threshold=32)
    assert "No names" in text


def test_scan_oversold_filters_with_injected_closes(monkeypatch):
    dates = pd.bdate_range(end=datetime(2026, 8, 11), periods=40)

    def fake_fetch(symbols, history_days):
        # Build a falling series so RSI is low for OVER, flat high for SKIP.
        over = pd.Series(
            [100 - i for i in range(len(dates))],
            index=dates,
            dtype=float,
        )
        skip = pd.Series([100.0] * len(dates), index=dates, dtype=float)
        return {"OVER": over, "SKIP": skip}

    monkeypatch.setattr("src.us_rsi.scanner.fetch_daily_closes", fake_fetch)

    hits = scan_oversold(
        ["OVER", "SKIP"],
        rsi_period=14,
        rsi_threshold=32,
        history_days=60,
    )
    assert [h.symbol for h in hits] == ["OVER"]
    assert hits[0].rsi <= 32


def test_split_adjustment_removes_fake_crash():
    """MNST-style 2-for-1: raw close halves; adjusted series stays continuous."""
    idx = pd.to_datetime(["2026-08-06", "2026-08-07", "2026-08-12"])
    close = pd.Series([94.16, 90.36, 45.18], index=idx, dtype=float)
    splits = pd.Series([2.0], index=pd.to_datetime(["2026-08-11"]))

    adjusted = apply_split_adjustment(close, splits)
    # Pre-split bars are halved; post-split bar unchanged.
    assert adjusted.loc[pd.Timestamp("2026-08-07")] == 45.18
    assert adjusted.loc[pd.Timestamp("2026-08-12")] == 45.18
    # No ~50% cliff into the latest bar.
    assert abs(adjusted.pct_change().iloc[-1]) < 0.01
