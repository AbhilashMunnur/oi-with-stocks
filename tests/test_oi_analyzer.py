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


def test_call_short_requires_change_pcr_strictly_below_point_75():
    at_limit = _call_oi(call_oi_change=200, put_oi_change=150)
    above_limit = _call_oi(call_oi_change=200, put_oi_change=160)

    assert call_oi_flow_rejection(at_limit, max_change_pcr=0.75) is not None
    assert call_oi_flow_rejection(above_limit, max_change_pcr=0.75) is not None


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


def test_put_long_requires_change_pcr_strictly_above_one():
    oi = _call_oi(call_oi_change=200, put_oi_change=200)

    assert put_oi_flow_rejection(oi, min_change_pcr=1.0) is not None


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


def test_put_support_ignores_broken_put_wall_above_price():
    from src.oi_analyzer import select_active_oi_walls

    legs = {
        26000.0: {"CE": (422, "CE-26000"), "PE": (311, "PE-26000")},
        24000.0: {"CE": (145, "CE-24000"), "PE": (302, "PE-24000")},
        27000.0: {"CE": (691, "CE-27000"), "PE": (50, "PE-27000")},
        24500.0: {"CE": (583, "CE-24500"), "PE": (215, "PE-24500")},
    }

    call_wall, put_wall = select_active_oi_walls(legs, ltp=24300.0)

    assert put_wall is not None and put_wall[0] == 24000.0
    assert call_wall is not None and call_wall[0] == 27000.0


def test_put_support_includes_strike_equal_to_spot():
    from src.oi_analyzer import select_active_oi_walls

    legs = {
        24100.0: {"PE": (400, "PE-24100")},
        24000.0: {"PE": (300, "PE-24000")},
        26000.0: {"PE": (500, "PE-26000")},
    }

    _call_wall, put_wall = select_active_oi_walls(legs, ltp=24100.0)

    assert put_wall is not None and put_wall[0] == 24100.0


def test_coforge_style_call_writing_is_not_a_broken_resistance():
    from src.oi_analyzer import resistance_is_broken

    oi = _call_oi(ltp=1810, max_call_oi_strike=1800, call_oi_change=111, put_oi_change=-1)
    assert resistance_is_broken(oi) is False


def test_call_unwind_without_price_cross_is_not_broken_resistance():
    from src.oi_analyzer import resistance_is_broken

    oi = _call_oi(ltp=1790, max_call_oi_strike=1800, call_oi_change=-50, put_oi_change=80)
    assert resistance_is_broken(oi) is False


def test_call_unwind_and_put_add_after_price_cross_is_broken_resistance():
    from src.oi_analyzer import resistance_is_broken

    oi = _call_oi(ltp=1810, max_call_oi_strike=1800, call_oi_change=-50, put_oi_change=80)
    assert resistance_is_broken(oi) is True


def test_rec_style_put_unwind_and_call_add_below_strike_is_broken_support():
    from src.oi_analyzer import support_is_broken

    oi = _call_oi(ltp=330, max_put_oi_strike=345, call_oi_change=200, put_oi_change=-50)
    assert support_is_broken(oi) is True


def test_put_unwind_without_price_cross_is_not_broken_support():
    from src.oi_analyzer import support_is_broken

    oi = _call_oi(ltp=350, max_put_oi_strike=345, call_oi_change=200, put_oi_change=-50)
    assert support_is_broken(oi) is False


def test_migrated_call_wall_is_not_a_break_of_the_entry_strike():
    """Short entered vs Call 110. Price later 103 vs a new 102 Call wall is not a 110 break."""
    from src.oi_analyzer import resistance_is_broken

    entry = _call_oi(ltp=103, max_call_oi_strike=110, call_oi_change=-80, put_oi_change=60)
    moved = _call_oi(ltp=103, max_call_oi_strike=102, call_oi_change=-80, put_oi_change=60)

    assert resistance_is_broken(entry, 103) is False
    assert resistance_is_broken(moved, 103) is True


def test_s1_entry_uses_uncrossed_call_not_peak_already_through():
    from src.oi_analyzer import choose_s1_entry_wall

    legs = {
        1800.0: {"CE": (800_000, "ce-1800")},
        1900.0: {"CE": (500_000, "ce-1900")},
    }

    wall = choose_s1_entry_wall(legs, ltp=1810.0, side="call", min_fallback_oi_pct=50.0)

    assert wall is not None and wall[0] == 1900.0


def test_s1_entry_skips_thin_uncrossed_call_fallback():
    from src.oi_analyzer import choose_s1_entry_wall

    legs = {
        1800.0: {"CE": (800_000, "ce-1800")},
        1900.0: {"CE": (50_000, "ce-1900")},
    }

    wall = choose_s1_entry_wall(legs, ltp=1810.0, side="call", min_fallback_oi_pct=50.0)

    assert wall is None


def test_s1_entry_keeps_peak_call_when_price_has_not_crossed():
    from src.oi_analyzer import choose_s1_entry_wall

    legs = {
        1800.0: {"CE": (800_000, "ce-1800")},
        1900.0: {"CE": (500_000, "ce-1900")},
    }

    wall = choose_s1_entry_wall(legs, ltp=1790.0, side="call")

    assert wall is not None and wall[0] == 1800.0


def test_s1_entry_uses_uncrossed_put_not_peak_already_through():
    from src.oi_analyzer import choose_s1_entry_wall

    legs = {
        345.0: {"PE": (800_000, "pe-345")},
        340.0: {"PE": (500_000, "pe-340")},
    }

    wall = choose_s1_entry_wall(legs, ltp=342.0, side="put", min_fallback_oi_pct=50.0)

    assert wall is not None and wall[0] == 340.0


def test_proximity_skip_reason_when_more_than_one_percent_away():
    from src.oi_analyzer import proximity_skip_reason

    price = PriceSnapshot(symbol="HDFCBANK", ltp=1650.0, rsi=71.4)
    oi = _call_oi(ltp=1650.0, max_call_oi_strike=1720.0)
    reason = proximity_skip_reason(price, oi, SignalType.CALL_OI, 1.0)

    assert reason is not None
    assert "Call" in reason
    assert "1%" in reason or "1.0" in reason


def test_exactly_one_percent_away_still_counts_as_near():
    from src.oi_analyzer import proximity_skip_reason

    price = PriceSnapshot(symbol="JUBLFOOD", ltp=495.0, rsi=70.3)
    oi = _call_oi(ltp=495.0, max_call_oi_strike=500.0)
    assert proximity_skip_reason(price, oi, SignalType.CALL_OI, 1.0) is None
