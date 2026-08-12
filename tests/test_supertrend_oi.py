import pandas as pd

from src.config import OIConfig, SignalType, SupertrendConfig
from src.data.models import OISnapshot
from src.indicators import calculate_supertrend
from src.supertrend_oi import (
    evaluate_supertrend_oi,
    near_supertrend_from_above,
    near_supertrend_from_below,
)


def test_supertrend_returns_value_on_trending_series():
    n = 80
    close = pd.Series([100 + i * 0.5 for i in range(n)], dtype=float)
    high = close + 1
    low = close - 1
    st, side = calculate_supertrend(high, low, close, period=10, multiplier=3.0)
    assert st is not None
    assert side in {"above", "below"}


def test_near_supertrend_sides():
    assert near_supertrend_from_below(99.7, 100.0, 0.5) is True
    assert near_supertrend_from_below(100.1, 100.0, 0.5) is False
    assert near_supertrend_from_above(100.3, 100.0, 0.5) is True
    assert near_supertrend_from_above(99.9, 100.0, 0.5) is False


def test_bearish_st_alert_requires_call_writing():
    oi = OISnapshot(
        symbol="TEST",
        ltp=99.7,
        max_call_oi_strike=100,
        max_call_oi=10_000,
        max_put_oi_strike=100,
        max_put_oi=8_000,
        expiry="2026-08-28",
        lot_size=100,
        call_oi_change=500,
        put_oi_change=200,
    )
    alert = evaluate_supertrend_oi(
        symbol="TEST",
        ltp=99.7,
        supertrend=100.0,
        side="below",
        oi=oi,
        st_config=SupertrendConfig(proximity_pct=0.5),
        oi_config=OIConfig(proximity_pct=1.0),
    )
    assert alert is not None
    assert alert.signal is SignalType.ST_BEARISH


def test_bearish_st_skipped_on_call_unwind():
    oi = OISnapshot(
        symbol="TEST",
        ltp=99.7,
        max_call_oi_strike=100,
        max_call_oi=10_000,
        max_put_oi_strike=100,
        max_put_oi=8_000,
        expiry="2026-08-28",
        lot_size=100,
        call_oi_change=-500,
        put_oi_change=200,
    )
    alert = evaluate_supertrend_oi(
        symbol="TEST",
        ltp=99.7,
        supertrend=100.0,
        side="below",
        oi=oi,
        st_config=SupertrendConfig(proximity_pct=0.5),
        oi_config=OIConfig(proximity_pct=1.0),
    )
    assert alert is None


def test_bullish_st_alert_requires_put_writing():
    oi = OISnapshot(
        symbol="TEST",
        ltp=100.3,
        max_call_oi_strike=100,
        max_call_oi=10_000,
        max_put_oi_strike=100,
        max_put_oi=8_000,
        expiry="2026-08-28",
        lot_size=100,
        call_oi_change=100,
        put_oi_change=400,
    )
    alert = evaluate_supertrend_oi(
        symbol="TEST",
        ltp=100.3,
        supertrend=100.0,
        side="above",
        oi=oi,
        st_config=SupertrendConfig(proximity_pct=0.5),
        oi_config=OIConfig(proximity_pct=1.0),
    )
    assert alert is not None
    assert alert.signal is SignalType.ST_BULLISH
