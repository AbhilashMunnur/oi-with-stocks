from src.candle_patterns import (
    Candle,
    candle_stop_price,
    day2_long_pattern,
    day2_short_pattern,
    is_hammer,
    is_inverted_hammer,
    is_strong_bear,
    is_strong_bull,
    is_weak_middle,
    reversal_setup,
    same_day_setup,
    waiting_reason,
    wider_of_candle_and_pct,
    with_live_close,
)
from src.config import CandleConfig, SignalType, load_config


CFG = CandleConfig()


def bar(open_: float, high: float, low: float, close: float, day="2026-08-28") -> Candle:
    return Candle(date=day, open=open_, high=high, low=low, close=close)


def test_strong_bull_is_green_body_at_least_60_percent():
    assert is_strong_bull(bar(100, 110, 99, 109), CFG)
    assert not is_strong_bull(bar(100, 110, 99, 98), CFG)
    # Body only ~36% of the range.
    assert not is_strong_bull(bar(100, 120, 90, 111), CFG)


def test_strong_red_is_bear_body_at_least_60_percent():
    assert is_strong_bear(bar(110, 111, 100, 101), CFG)
    assert not is_strong_bear(bar(100, 111, 100, 109), CFG)


def test_inverted_hammer_has_long_upper_stick_and_body_near_the_low():
    hammer = bar(100, 120, 99, 102)
    assert is_inverted_hammer(hammer, CFG)
    assert day2_short_pattern(hammer, CFG) == "inverted hammer"
    assert not is_weak_middle(hammer, CFG)


def test_weak_middle_has_body_in_the_middle_and_wicks_both_sides():
    weak = bar(100, 120, 84, 104)
    assert is_weak_middle(weak, CFG)
    assert day2_short_pattern(weak, CFG) == "weak middle body"
    assert not is_inverted_hammer(weak, CFG)


def test_hammer_is_the_long_side_inverse():
    hammer = bar(118, 120, 100, 116)
    assert is_hammer(hammer, CFG)
    assert day2_long_pattern(hammer, CFG) == "hammer"


def test_does_not_short_on_rsi_70_alone_without_reversal():
    stretch = bar(100, 110, 99, 109, "2026-08-27")
    follow = bar(109, 118, 108, 117, "2026-08-28")
    assert reversal_setup(
        stretch, follow, 74.0, call_threshold=70, put_threshold=31, cfg=CFG
    ) is None
    assert waiting_reason(
        stretch, 74.0, call_threshold=70, put_threshold=31, cfg=CFG
    )


def test_shorts_only_after_one_of_the_three_bearish_days():
    stretch = bar(100, 110, 99, 109, "2026-08-27")
    red = bar(110, 111, 100, 101, "2026-08-28")
    got = reversal_setup(
        stretch, red, 70.0, call_threshold=70, put_threshold=31, cfg=CFG
    )
    assert got == (SignalType.RSI_CANDLE_SHORT, "strong red body")


def test_next_day_short_after_rsi_70_even_without_strong_bull_stretch():
    """HINDALCO-style: ~49% green at RSI 70, then strong red → short."""
    stretch = bar(100, 110, 99, 105.4, "2026-08-12")  # body ~49% of range
    assert not is_strong_bull(stretch, CFG)
    red = bar(105, 106, 95, 96, "2026-08-13")
    got = reversal_setup(
        stretch, red, 71.0, call_threshold=70, put_threshold=30, cfg=CFG
    )
    assert got == (SignalType.RSI_CANDLE_SHORT, "strong red body")


def test_longs_only_after_rsi_30_then_reversal():
    stretch = bar(110, 111, 100, 101, "2026-08-27")
    hammer = bar(101, 103, 90, 102, "2026-08-28")
    got = reversal_setup(
        stretch, hammer, 28.0, call_threshold=70, put_threshold=31, cfg=CFG
    )
    assert got == (SignalType.RSI_CANDLE_LONG, "hammer")


def test_next_day_long_after_rsi_30_even_without_strong_bear_stretch():
    stretch = bar(110, 112, 100, 104.8, "2026-08-27")  # red but not ≥50% body
    assert not is_strong_bear(stretch, CFG)
    green = bar(104, 115, 103, 114, "2026-08-28")
    got = reversal_setup(
        stretch, green, 28.0, call_threshold=70, put_threshold=30, cfg=CFG
    )
    assert got == (SignalType.RSI_CANDLE_LONG, "strong green body")


def test_rsi_69_is_not_a_short_setup():
    stretch = bar(100, 110, 99, 109, "2026-08-27")
    red = bar(110, 111, 100, 101, "2026-08-28")
    assert (
        reversal_setup(
            stretch, red, 69.0, call_threshold=70, put_threshold=31, cfg=CFG
        )
        is None
    )


def test_live_close_keeps_open_and_stretches_high_low():
    forming = with_live_close(bar(100, 105, 99, 104), 108.0)
    assert forming.open == 100
    assert forming.close == 108
    assert forming.high == 108


def test_same_day_shorts_when_rsi_tagged_70_and_bar_reverses():
    red = bar(110, 111, 100, 101, "2026-08-31")
    got = same_day_setup(
        red,
        rsi_at_close=68.0,
        rsi_at_high=72.0,
        rsi_at_low=65.0,
        call_threshold=70,
        put_threshold=30,
        cfg=CFG,
    )
    assert got == (SignalType.RSI_CANDLE_SHORT, "strong red body")


def test_same_day_does_not_short_a_strong_bull_at_rsi_70():
    bull = bar(100, 110, 99, 109, "2026-08-31")
    assert (
        same_day_setup(
            bull,
            rsi_at_close=74.0,
            rsi_at_high=75.0,
            rsi_at_low=70.0,
            call_threshold=70,
            put_threshold=30,
            cfg=CFG,
        )
        is None
    )


def test_same_day_longs_when_rsi_tagged_30_and_bar_reverses():
    green = bar(100, 111, 99, 109, "2026-08-31")
    got = same_day_setup(
        green,
        rsi_at_close=32.0,
        rsi_at_high=35.0,
        rsi_at_low=28.0,
        call_threshold=70,
        put_threshold=30,
        cfg=CFG,
    )
    assert got == (SignalType.RSI_CANDLE_LONG, "strong green body")


def test_either_scenario_is_enough_to_take():
    """One fill if next-day or same-day qualifies; both is still one trade."""
    stretch = bar(100, 110, 99, 109, "2026-08-27")
    red = bar(110, 111, 100, 101, "2026-08-28")
    next_day = reversal_setup(
        stretch, red, 70.0, call_threshold=70, put_threshold=30, cfg=CFG
    )
    same = same_day_setup(
        red,
        rsi_at_close=65.0,
        rsi_at_high=76.0,
        rsi_at_low=62.0,
        call_threshold=70,
        put_threshold=30,
        cfg=CFG,
    )
    assert next_day is not None
    assert same is not None
    assert next_day or same


def test_two_candle_short_stop_is_the_prior_bar_high():
    stretch = bar(100, 110, 99, 109, "2026-08-31")
    doji = bar(108, 109, 100, 102, "2026-09-01")
    assert candle_stop_price(
        SignalType.RSI_CANDLE_SHORT, reversal=doji, prior=stretch, same_day=False
    ) == 110


def test_same_day_short_stop_is_the_reversal_high():
    doji = bar(108, 112, 100, 102, "2026-09-01")
    assert candle_stop_price(
        SignalType.RSI_CANDLE_SHORT, reversal=doji, same_day=True
    ) == 112


def test_two_candle_long_stop_is_the_prior_bar_low():
    stretch = bar(110, 111, 95, 96, "2026-08-31")
    hammer = bar(97, 108, 96, 107, "2026-09-01")
    assert candle_stop_price(
        SignalType.RSI_CANDLE_LONG, reversal=hammer, prior=stretch, same_day=False
    ) == 95


def test_same_day_long_stop_is_the_reversal_low():
    hammer = bar(97, 108, 90, 107, "2026-09-01")
    assert candle_stop_price(
        SignalType.RSI_CANDLE_LONG, reversal=hammer, same_day=True
    ) == 90


def test_config_loads_candle_thresholds():
    cfg = load_config()
    assert cfg.candles.strong_body_pct == 50.0
    assert cfg.candles.weak_body_pct == 40.0
    assert cfg.candles.side_wick_pct == 20.0


def test_short_stop_uses_two_percent_when_the_high_is_tighter():
    assert wider_of_candle_and_pct(
        "SHORT", entry=100.0, candle_stop=100.80, pct=2.0
    ) == 102.0


def test_short_stop_uses_the_high_when_it_is_farther_than_two_percent():
    assert wider_of_candle_and_pct(
        "SHORT", entry=100.0, candle_stop=104.0, pct=2.0
    ) == 104.0


def test_long_stop_uses_two_percent_when_the_low_is_tighter():
    assert wider_of_candle_and_pct(
        "LONG", entry=100.0, candle_stop=99.40, pct=2.0
    ) == 98.0


def test_long_stop_uses_the_low_when_it_is_farther_than_two_percent():
    assert wider_of_candle_and_pct(
        "LONG", entry=100.0, candle_stop=95.0, pct=2.0
    ) == 95.0
