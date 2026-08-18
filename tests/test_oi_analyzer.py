from src.config import SignalType
from src.data.models import OISnapshot, PriceSnapshot
from src.oi_analyzer import (
    align_snapshot_to_reference_strike,
    call_oi_flow_rejection,
    evaluate_stock,
    put_oi_flow_rejection,
)


def _call_oi(**kwargs) -> OISnapshot:
    base = dict(
        symbol="RELIANCE",
        ltp=1320,
        max_call_oi_strike=1320,
        max_call_oi=50000,
        max_put_oi_strike=1280,
        max_put_oi=30000,
        expiry="2026-08-25",
        call_oi_change=10_000,
        put_oi_change=5_000,
    )
    base.update(kwargs)
    return OISnapshot(**base)


def test_call_oi_alert_when_rsi_high_and_near_max_call_strike():
    price = PriceSnapshot(symbol="RELIANCE", ltp=1320, rsi=72.5)
    alert = evaluate_stock(
        price, _call_oi(), rsi_call_threshold=70, rsi_put_threshold=35, proximity_pct=2.0
    )

    assert alert is not None
    assert alert.signal == SignalType.CALL_OI


def test_call_oi_alert_when_rsi_exactly_70():
    price = PriceSnapshot(symbol="RELIANCE", ltp=1320, rsi=70.0)
    alert = evaluate_stock(
        price, _call_oi(), rsi_call_threshold=70, rsi_put_threshold=35, proximity_pct=2.0
    )

    assert alert is not None
    assert alert.signal == SignalType.CALL_OI


def test_put_oi_alert_when_rsi_low_and_near_max_put_strike():
    price = PriceSnapshot(symbol="RELIANCE", ltp=1285, rsi=28.0)
    oi = OISnapshot(
        symbol="RELIANCE",
        ltp=1285,
        max_call_oi_strike=1400,
        max_call_oi=50000,
        max_put_oi_strike=1280,
        max_put_oi=80000,
        expiry="2026-08-25",
        put_oi_change=10_000,
        call_oi_change=5_000,
    )

    alert = evaluate_stock(price, oi, rsi_call_threshold=70, rsi_put_threshold=35, proximity_pct=2.0)

    assert alert is not None
    assert alert.signal == SignalType.PUT_OI


def test_put_oi_alert_when_rsi_exactly_35():
    price = PriceSnapshot(symbol="RELIANCE", ltp=1285, rsi=35.0)
    oi = OISnapshot(
        symbol="RELIANCE",
        ltp=1285,
        max_call_oi_strike=1400,
        max_call_oi=50000,
        max_put_oi_strike=1280,
        max_put_oi=80000,
        expiry="2026-08-25",
        put_oi_change=10_000,
        call_oi_change=5_000,
    )

    alert = evaluate_stock(price, oi, rsi_call_threshold=70, rsi_put_threshold=35, proximity_pct=2.0)

    assert alert is not None
    assert alert.signal == SignalType.PUT_OI


def test_no_alert_when_rsi_high_but_far_from_call_strike():
    price = PriceSnapshot(symbol="RELIANCE", ltp=1200, rsi=75.0)
    oi = _call_oi(ltp=1200, max_call_oi_strike=1400, max_put_oi_strike=1180)

    alert = evaluate_stock(price, oi, rsi_call_threshold=70, rsi_put_threshold=35, proximity_pct=2.0)

    assert alert is None


def test_call_short_skipped_when_calls_unwind():
    """PAYTM-style: overbought near max Call OI but Call ΔOI negative."""
    price = PriceSnapshot(symbol="PAYTM", ltp=1608, rsi=84.0)
    oi = OISnapshot(
        symbol="PAYTM",
        ltp=1608,
        max_call_oi_strike=1600,
        max_call_oi=100_000,
        max_put_oi_strike=1500,
        max_put_oi=80_000,
        expiry="2026-08-25",
        lot_size=1,
        call_oi_change=-273,
        put_oi_change=473,
    )

    assert call_oi_flow_rejection(oi) is not None
    assert evaluate_stock(price, oi, 70, 35, 1.0) is None


def test_call_short_skipped_when_put_writing_dominates():
    """DIVISLAB-style: Call writing positive but Put writing stronger (ΔPCR > 1)."""
    price = PriceSnapshot(symbol="DIVISLAB", ltp=8491.5, rsi=76.4)
    oi = OISnapshot(
        symbol="DIVISLAB",
        ltp=8491.5,
        max_call_oi_strike=8500,
        max_call_oi=100_000,
        max_put_oi_strike=8400,
        max_put_oi=90_000,
        expiry="2026-08-25",
        lot_size=1,
        call_oi_change=179,
        put_oi_change=203,
    )

    assert oi.change_pcr is not None and oi.change_pcr > 1.0
    assert call_oi_flow_rejection(oi) is not None
    assert evaluate_stock(price, oi, 70, 35, 1.0) is None


def test_call_short_allowed_when_call_writing_leads():
    price = PriceSnapshot(symbol="RELIANCE", ltp=1320, rsi=72.0)
    oi = _call_oi(call_oi_change=200, put_oi_change=100)

    assert evaluate_stock(price, oi, 70, 35, 2.0) is not None


def test_call_short_allowed_when_puts_unwind_but_calls_write():
    price = PriceSnapshot(symbol="RELIANCE", ltp=1320, rsi=72.0)
    oi = _call_oi(call_oi_change=200, put_oi_change=-50)

    assert oi.change_pcr is None
    assert evaluate_stock(price, oi, 70, 35, 2.0) is not None


def test_put_long_skipped_when_puts_unwind():
    price = PriceSnapshot(symbol="BLUESTARCO", ltp=1499.9, rsi=31.9)
    oi = OISnapshot(
        symbol="BLUESTARCO",
        ltp=1499.9,
        max_call_oi_strike=1600,
        max_call_oi=50_000,
        max_put_oi_strike=1500,
        max_put_oi=80_000,
        expiry="2026-08-25",
        lot_size=1,
        call_oi_change=14,
        put_oi_change=-33,
    )

    assert put_oi_flow_rejection(oi) is not None
    assert evaluate_stock(price, oi, 70, 35, 1.0) is None


def test_put_long_skipped_when_call_writing_dominates():
    price = PriceSnapshot(symbol="LICHSGFIN", ltp=501.95, rsi=31.6)
    oi = OISnapshot(
        symbol="LICHSGFIN",
        ltp=501.95,
        max_call_oi_strike=520,
        max_call_oi=50_000,
        max_put_oi_strike=500,
        max_put_oi=80_000,
        expiry="2026-08-25",
        lot_size=1,
        call_oi_change=200,
        put_oi_change=100,
    )

    assert oi.change_pcr is not None and oi.change_pcr < 1.0
    assert put_oi_flow_rejection(oi) is not None
    assert evaluate_stock(price, oi, 70, 35, 1.0) is None


def test_put_long_allowed_when_put_writing_leads():
    price = PriceSnapshot(symbol="RELIANCE", ltp=1285, rsi=28.0)
    oi = OISnapshot(
        symbol="RELIANCE",
        ltp=1285,
        max_call_oi_strike=1400,
        max_call_oi=50_000,
        max_put_oi_strike=1280,
        max_put_oi=80_000,
        expiry="2026-08-25",
        call_oi_change=100,
        put_oi_change=200,
    )

    assert evaluate_stock(price, oi, 70, 35, 2.0) is not None


def test_put_long_allowed_when_calls_unwind_but_puts_write():
    price = PriceSnapshot(symbol="RELIANCE", ltp=1285, rsi=28.0)
    oi = OISnapshot(
        symbol="RELIANCE",
        ltp=1285,
        max_call_oi_strike=1400,
        max_call_oi=50_000,
        max_put_oi_strike=1280,
        max_put_oi=80_000,
        expiry="2026-08-25",
        call_oi_change=-50,
        put_oi_change=200,
    )

    assert oi.change_pcr is None
    assert evaluate_stock(price, oi, 70, 35, 2.0) is not None


def test_align_call_reference_binds_both_legs_at_max_call_strike():
    oi = OISnapshot(
        symbol="X",
        ltp=100,
        max_call_oi_strike=100.5,
        max_call_oi=10_000,
        max_put_oi_strike=95.0,
        max_put_oi=20_000,
        expiry="2026-08-25",
        max_call_token="CE-MAX",
        max_put_token="PE-MAX",
        legs_by_strike={
            100.5: {"CE": (10_000, "CE-100.5"), "PE": (3_000, "PE-100.5")},
            95.0: {"CE": (1_000, "CE-95"), "PE": (20_000, "PE-95")},
        },
    )

    assert align_snapshot_to_reference_strike(oi, "call") is True
    assert oi.max_call_token == "CE-100.5"
    assert oi.max_put_token == "PE-100.5"
    assert oi.max_call_oi == 10_000
    assert oi.max_put_oi == 3_000
    assert oi.max_put_oi_strike == 100.5


def test_align_put_reference_binds_both_legs_at_max_put_strike():
    oi = OISnapshot(
        symbol="X",
        ltp=100,
        max_call_oi_strike=100.5,
        max_call_oi=10_000,
        max_put_oi_strike=95.0,
        max_put_oi=20_000,
        expiry="2026-08-25",
        max_call_token="CE-MAX",
        max_put_token="PE-MAX",
        legs_by_strike={
            100.5: {"CE": (10_000, "CE-100.5"), "PE": (3_000, "PE-100.5")},
            95.0: {"CE": (1_500, "CE-95"), "PE": (20_000, "PE-95")},
        },
    )

    assert align_snapshot_to_reference_strike(oi, "put") is True
    assert oi.max_call_token == "CE-95"
    assert oi.max_put_token == "PE-95"
    assert oi.max_call_oi == 1_500
    assert oi.max_put_oi == 20_000
    assert oi.max_call_oi_strike == 95.0
