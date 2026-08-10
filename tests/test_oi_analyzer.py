from src.config import SignalType
from src.data.models import OISnapshot, PriceSnapshot
from src.oi_analyzer import evaluate_stock


def test_call_oi_alert_when_rsi_high_and_near_max_call_strike():
    price = PriceSnapshot(symbol="RELIANCE", ltp=1320, rsi=72.5)
    oi = OISnapshot(
        symbol="RELIANCE",
        ltp=1320,
        max_call_oi_strike=1320,
        max_call_oi=50000,
        max_put_oi_strike=1280,
        max_put_oi=30000,
        expiry="2026-08-25",
    )

    alert = evaluate_stock(price, oi, rsi_call_threshold=70, rsi_put_threshold=35, proximity_pct=2.0)

    assert alert is not None
    assert alert.signal == SignalType.CALL_OI


def test_call_oi_alert_when_rsi_exactly_70():
    price = PriceSnapshot(symbol="RELIANCE", ltp=1320, rsi=70.0)
    oi = OISnapshot(
        symbol="RELIANCE",
        ltp=1320,
        max_call_oi_strike=1320,
        max_call_oi=50000,
        max_put_oi_strike=1280,
        max_put_oi=30000,
        expiry="2026-08-25",
    )

    alert = evaluate_stock(price, oi, rsi_call_threshold=70, rsi_put_threshold=35, proximity_pct=2.0)

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
    )

    alert = evaluate_stock(price, oi, rsi_call_threshold=70, rsi_put_threshold=35, proximity_pct=2.0)

    assert alert is not None
    assert alert.signal == SignalType.PUT_OI


def test_no_alert_when_rsi_high_but_far_from_call_strike():
    price = PriceSnapshot(symbol="RELIANCE", ltp=1200, rsi=75.0)
    oi = OISnapshot(
        symbol="RELIANCE",
        ltp=1200,
        max_call_oi_strike=1400,
        max_call_oi=50000,
        max_put_oi_strike=1180,
        max_put_oi=30000,
        expiry="2026-08-25",
    )

    alert = evaluate_stock(price, oi, rsi_call_threshold=70, rsi_put_threshold=35, proximity_pct=2.0)

    assert alert is None
