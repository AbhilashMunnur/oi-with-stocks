"""Compatibility tests for the US RSI package aliases."""

from src.us_rsi import format_us_rsi_digest, load_us_rsi_config
from src.daily_rsi import RsiHit


def test_us_config_loads():
    cfg = load_us_rsi_config()
    assert cfg.rsi_threshold == 32
    assert cfg.weekly_enabled is True
    assert cfg.weekly_threshold == 35
    assert "nasdaq100" in cfg.watchlist_path


def test_us_digest_alias():
    text = format_us_rsi_digest(
        [RsiHit("AAPL", 30.0, 100.0, "2026-08-12")], threshold=32
    )
    assert "Nasdaq" in text
    assert "AAPL" in text
