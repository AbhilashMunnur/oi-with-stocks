from datetime import datetime

import pandas as pd

from src.daily_rsi import RsiHit, apply_split_adjustment, format_rsi_digest, scan_oversold


def test_format_digest_lists_hits_lowest_rsi_first():
    hits = [
        RsiHit("AAA", 28.0, 10.0, "2026-08-11"),
        RsiHit("BBB", 31.5, 20.0, "2026-08-11"),
    ]
    text = format_rsi_digest(
        hits, threshold=32, market_label="India Nifty-200", currency_symbol="₹"
    )
    assert "RSI ≤ 32" in text
    assert "₹" in text
    assert text.index("AAA") < text.index("BBB")


def test_format_digest_when_empty():
    text = format_rsi_digest([], threshold=32, market_label="US Nasdaq-100")
    assert "No names" in text


def test_scan_oversold_filters_with_injected_closes(monkeypatch):
    dates = pd.bdate_range(end=datetime(2026, 8, 11), periods=40)

    def fake_fetch(symbols, history_days, yahoo_suffix=""):
        over = pd.Series([100 - i for i in range(len(dates))], index=dates, dtype=float)
        skip = pd.Series([100.0] * len(dates), index=dates, dtype=float)
        return {"OVER": over, "SKIP": skip}

    monkeypatch.setattr("src.daily_rsi.scanner.fetch_daily_closes", fake_fetch)

    hits = scan_oversold(
        ["OVER", "SKIP"],
        rsi_period=14,
        rsi_threshold=32,
        history_days=60,
        yahoo_suffix=".NS",
    )
    assert [h.symbol for h in hits] == ["OVER"]
    assert hits[0].rsi <= 32


def test_split_adjustment_removes_fake_crash():
    idx = pd.to_datetime(["2026-08-06", "2026-08-07", "2026-08-12"])
    close = pd.Series([94.16, 90.36, 45.18], index=idx, dtype=float)
    splits = pd.Series([2.0], index=pd.to_datetime(["2026-08-11"]))

    adjusted = apply_split_adjustment(close, splits)
    assert adjusted.loc[pd.Timestamp("2026-08-07")] == 45.18
    assert adjusted.loc[pd.Timestamp("2026-08-12")] == 45.18
    assert abs(adjusted.pct_change().iloc[-1]) < 0.01
