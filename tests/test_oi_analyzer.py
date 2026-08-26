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


def test_laboratory_short_is_blocked_pharma_is_not():
    from src.oi_analyzer import no_short_skip_reason

    labs = ["DIVISLAB", "LAURUSLABS"]
    assert (
        no_short_skip_reason("DIVISLAB", labs, is_short=True)
        == "laboratory — longs only"
    )
    assert (
        no_short_skip_reason("lauruslabs", labs, is_short=True)
        == "laboratory — longs only"
    )
    assert no_short_skip_reason("DIVISLAB", labs, is_short=False) is None
    assert no_short_skip_reason("LUPIN", labs, is_short=True) is None
    assert no_short_skip_reason("SUNPHARMA", labs, is_short=True) is None


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


def test_s2_half_percent_proximity_rejects_what_s1_would_take():
    from src.oi_analyzer import proximity_skip_reason

    # 0.80% from 1700 — inside S1's 1%, outside S2's 0.5%.
    price = PriceSnapshot(symbol="PAYTM", ltp=1713.6, rsi=75.8)
    oi = _call_oi(ltp=1713.6, max_call_oi_strike=1700.0)
    assert proximity_skip_reason(price, oi, SignalType.CALL_OI, 1.0) is None
    reason = proximity_skip_reason(price, oi, SignalType.CALL_OI, 0.5)
    assert reason is not None
    assert "0.5%" in reason


def test_s2_half_percent_proximity_still_takes_half_percent():
    from src.oi_analyzer import proximity_skip_reason

    price = PriceSnapshot(symbol="PAYTM", ltp=1708.5, rsi=75.8)
    oi = _call_oi(ltp=1708.5, max_call_oi_strike=1700.0)
    assert proximity_skip_reason(price, oi, SignalType.CALL_OI, 0.5) is None


def test_s2_short_exits_when_cash_is_through_the_call_strike():
    from src.oi_analyzer import s2_invalidation_reason

    assert (
        s2_invalidation_reason(
            "SHORT", 1700.0, 1710.0, call_oi_change=700, put_oi_change=200
        )
        == "strike_through"
    )


def test_s2_short_exits_when_calls_are_covering_even_below_strike():
    from src.oi_analyzer import s2_invalidation_reason

    assert (
        s2_invalidation_reason(
            "SHORT", 1700.0, 1690.0, call_oi_change=-50, put_oi_change=80
        )
        == "writing_gone"
    )


def test_s2_short_holds_when_still_below_strike_and_calls_writing():
    from src.oi_analyzer import s2_invalidation_reason

    assert (
        s2_invalidation_reason(
            "SHORT", 1700.0, 1698.0, call_oi_change=727, put_oi_change=261
        )
        is None
    )


def test_s2_long_exits_when_cash_is_through_the_put_strike():
    from src.oi_analyzer import s2_invalidation_reason

    assert (
        s2_invalidation_reason(
            "LONG", 2160.0, 2150.0, call_oi_change=2, put_oi_change=16
        )
        == "strike_through"
    )


def test_s2_long_exits_when_puts_are_covering():
    from src.oi_analyzer import s2_invalidation_reason

    assert (
        s2_invalidation_reason(
            "LONG", 2160.0, 2180.0, call_oi_change=10, put_oi_change=0
        )
        == "writing_gone"
    )


def test_s2_does_not_treat_missing_delta_oi_as_covering():
    from src.oi_analyzer import s2_invalidation_reason

    assert (
        s2_invalidation_reason(
            "SHORT", 1700.0, 1690.0, call_oi_change=None, put_oi_change=None
        )
        is None
    )


def test_s2_through_strike_wins_over_writing_gone():
    from src.oi_analyzer import s2_invalidation_reason

    assert (
        s2_invalidation_reason(
            "SHORT", 1700.0, 1711.0, call_oi_change=-10, put_oi_change=5
        )
        == "strike_through"
    )


def test_s2_first_invalid_scan_does_not_exit():
    from src.oi_analyzer import s2_confirm_invalidation

    pending, exit_now = s2_confirm_invalidation("", "writing_gone", wall_valid=False)
    assert pending == "writing_gone"
    assert exit_now is False


def test_s2_second_invalid_scan_exits_even_if_reason_changes():
    from src.oi_analyzer import s2_confirm_invalidation

    pending, exit_now = s2_confirm_invalidation(
        "writing_gone", "strike_through", wall_valid=False
    )
    assert pending == "strike_through"
    assert exit_now is True


def test_s2_valid_wall_clears_first_invalidation():
    from src.oi_analyzer import s2_confirm_invalidation

    pending, exit_now = s2_confirm_invalidation(
        "writing_gone", None, wall_valid=True
    )
    assert pending == ""
    assert exit_now is False


def test_s2_missing_oi_does_not_clear_or_confirm():
    from src.oi_analyzer import s2_confirm_invalidation

    pending, exit_now = s2_confirm_invalidation(
        "writing_gone", None, wall_valid=False
    )
    assert pending == "writing_gone"
    assert exit_now is False


def test_s2_wall_still_valid_needs_writers_still_adding():
    from src.oi_analyzer import s2_wall_still_valid

    assert s2_wall_still_valid(
        "SHORT", 1700.0, 1698.0, call_oi_change=400, put_oi_change=100
    )
    assert not s2_wall_still_valid(
        "SHORT", 1700.0, 1698.0, call_oi_change=None, put_oi_change=100
    )
    assert not s2_wall_still_valid(
        "SHORT", 1700.0, 1710.0, call_oi_change=400, put_oi_change=100
    )


def test_retag_s1_alert_as_s2_copies_the_wall():
    from src.oi_analyzer import ScanAlert, retag_s1_alert_as_s2

    s1 = ScanAlert(
        symbol="PAYTM",
        signal=SignalType.CALL_OI_S1,
        ltp=1698.0,
        rsi=75.8,
        oi_strike=1700.0,
        oi_value=1,
        distance_pct=0.12,
        expiry="2026-09-29",
        message="PAYTM: RSI 75.8 vs max Call OI ₹1700 — taking position",
        lot_size=725,
    )
    s2 = retag_s1_alert_as_s2(s1)
    assert s2.signal == SignalType.CALL_OI_S2
    assert s2.oi_strike == 1700.0
    assert s2.ltp == s1.ltp
    assert s1.signal == SignalType.CALL_OI_S1


def test_s2_pcr_band_is_one_strike_below_wall_and_one_above():
    from src.oi_analyzer import strikes_around_wall

    legs = {k: {"CE": (1, f"ce-{k}")} for k in (1680.0, 1690.0, 1700.0, 1710.0, 1720.0)}
    assert strikes_around_wall(legs, 1700.0, n_below=1, n_above=1) == [
        1690.0,
        1700.0,
        1710.0,
    ]


def test_s2_short_needs_call_writing_at_the_wall_even_if_band_pcr_is_fine():
    oi = _call_oi(
        call_oi_change=-10,
        put_oi_change=2,
        band_call_oi_change=800,
        band_put_oi_change=100,
    )
    assert call_oi_flow_rejection(oi, require_change_pcr=True) is not None
    assert "unwinding" in call_oi_flow_rejection(oi, require_change_pcr=True)


def test_s2_short_uses_band_pcr_not_wall_pcr():
    # Wall PCR would be 0.5 (ok for a short); band PCR is 0.90 (too high).
    oi = _call_oi(
        call_oi_change=200,
        put_oi_change=100,
        band_call_oi_change=1000,
        band_put_oi_change=900,
    )
    rejected = call_oi_flow_rejection(oi, max_change_pcr=0.75, require_change_pcr=True)
    assert rejected is not None
    assert "band ΔPCR" in rejected


def test_s2_long_uses_band_pcr_and_requires_it():
    oi = _call_oi(
        call_oi_change=50,
        put_oi_change=80,
        band_call_oi_change=400,
        band_put_oi_change=200,
    )
    rejected = put_oi_flow_rejection(oi, min_change_pcr=1.0, require_change_pcr=True)
    assert rejected is not None
    assert "band ΔPCR" in rejected


def test_s2_skips_when_band_pcr_cannot_be_computed():
    oi = _call_oi(
        call_oi_change=200,
        put_oi_change=100,
        band_call_oi_change=500,
        band_put_oi_change=-20,
    )
    rejected = call_oi_flow_rejection(oi, require_change_pcr=True)
    assert rejected is not None
    assert "unavailable" in rejected


def test_s2_skips_a_thin_wall():
    from src.config import SignalType
    from src.oi_analyzer import s2_size_skip_reason

    oi = _call_oi(max_call_oi=50 * 175, call_oi_change=30 * 175, lot_size=175)
    reason = s2_size_skip_reason(
        oi, SignalType.CALL_OI, min_wall_contracts=100, min_write_contracts=20
    )
    assert reason is not None
    assert "too thin" in reason


def test_s2_skips_token_writing():
    from src.config import SignalType
    from src.oi_analyzer import s2_size_skip_reason

    oi = _call_oi(max_call_oi=200 * 175, call_oi_change=5 * 175, lot_size=175)
    reason = s2_size_skip_reason(
        oi, SignalType.CALL_OI, min_wall_contracts=100, min_write_contracts=20
    )
    assert reason is not None
    assert "too little writing" in reason


def test_s2_accepts_a_real_wall_and_writing():
    from src.config import SignalType
    from src.oi_analyzer import s2_size_skip_reason

    oi = _call_oi(max_call_oi=200 * 175, call_oi_change=40 * 175, lot_size=175)
    assert (
        s2_size_skip_reason(
            oi, SignalType.CALL_OI, min_wall_contracts=100, min_write_contracts=20
        )
        is None
    )



