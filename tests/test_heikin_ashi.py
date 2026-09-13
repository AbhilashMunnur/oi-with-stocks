from src.candle_patterns import Candle
from src.config import CandleConfig, HeikinAshiConfig, SignalType, load_config
from src.heikin_ashi import (
    ha_base_forming,
    ha_reversal_setup,
    ha_sequence,
    ha_watch_setup,
    heikin_ashi,
    is_strong,
    is_weak,
    make_ha_alert,
    rsi_tagged,
)

HA = HeikinAshiConfig()  # min_weak 0, any opposite body, no normal candle
STRICT = HeikinAshiConfig(
    min_weak_candles=1, opposite_needs_body=True, require_normal_candle=True
)
NORMAL = CandleConfig()


def bar(day, o, h, l, c):
    return Candle(f"2026-07-{day:02d}", o, h, l, c)


def test_heikin_ashi_formula_and_alignment():
    bars = [bar(1, 100, 110, 95, 105), bar(2, 105, 115, 104, 114)]
    ha = heikin_ashi(bars)

    assert [c.date for c in ha] == [b.date for b in bars]
    first, second = ha
    assert first.open == 102.5  # (open + close) / 2 on the first bar
    assert first.close == (100 + 110 + 95 + 105) / 4
    assert second.open == (first.open + first.close) / 2
    assert second.close == (105 + 115 + 104 + 114) / 4
    assert second.high == max(115, second.open, second.close)
    assert second.low == min(104, second.open, second.close)


def test_strong_and_weak_shapes():
    strong_green = Candle("d", 100, 110, 100, 109)  # 90% body, no lower wick
    weak = Candle("d", 104, 110, 100, 105)  # 10% body, sticks both sides
    assert is_strong(strong_green, HA, green=True)
    assert not is_strong(strong_green, HA, green=False)
    assert is_weak(weak, HA)
    assert not is_weak(strong_green, HA)


STRONG_GREEN = Candle("2026-07-01", 100, 110, 100, 109)
WEAK_1 = Candle("2026-07-02", 108, 114, 104, 110)  # body 20%
WEAK_2 = Candle("2026-07-03", 110, 115, 105, 111)  # body 10%
SOLID_RED = Candle("2026-07-04", 111, 112, 100, 102)  # body 75%
WEAK_RED = Candle("2026-07-04", 111, 115, 105, 110)  # body 10%


def test_short_sequence_strong_then_weak_then_red():
    seq = ha_sequence([STRONG_GREEN, WEAK_1, WEAK_2, SOLID_RED], HA, short=True)
    assert seq is not None
    assert seq.strong_date == "2026-07-01"
    assert seq.weak_count == 2
    assert seq.describe() == "HA strong 2026-07-01 → 2 weak → opposite body 75%"
    # Same candles do not qualify as a long.
    assert ha_sequence([STRONG_GREEN, WEAK_1, WEAK_2, SOLID_RED], HA, short=False) is None


def test_default_rule_takes_a_weak_red_and_needs_no_weak_run():
    # Weak opposite-colour candle is the entry (Bosch 24 Aug / 1 Sep case).
    seq = ha_sequence([STRONG_GREEN, WEAK_1, WEAK_RED], HA, short=True)
    assert seq is not None and seq.weak_count == 1
    # Strong run straight into a red candle also fires.
    seq = ha_sequence([STRONG_GREEN, SOLID_RED], HA, short=True)
    assert seq is not None and seq.weak_count == 0
    assert seq.describe() == "HA strong 2026-07-01 → opposite body 75%"
    # Two strong greens then red: the walk-back lands on the last strong one.
    seq = ha_sequence([STRONG_GREEN, Candle("2026-07-02", 109, 120, 109, 119), SOLID_RED], HA, short=True)
    assert seq is not None and seq.strong_date == "2026-07-02"


def test_strict_rule_rejects_weak_red_and_missing_weak_run():
    assert ha_sequence([STRONG_GREEN, WEAK_1, WEAK_RED], STRICT, short=True) is None
    assert ha_sequence([STRONG_GREEN, SOLID_RED], STRICT, short=True) is None
    assert ha_sequence([STRONG_GREEN, WEAK_1, SOLID_RED], STRICT, short=True) is not None


def test_no_strong_run_means_no_sequence():
    weak_only = [WEAK_1, WEAK_2, SOLID_RED]
    assert ha_sequence(weak_only, HA, short=True) is None
    # A strong RED before the run is the wrong colour for a short.
    strong_red_first = [Candle("a", 110, 110, 100, 101), WEAK_1, SOLID_RED]
    assert ha_sequence(strong_red_first, HA, short=True) is None


def test_base_forming_is_the_watch_state():
    reason = ha_base_forming([STRONG_GREEN, WEAK_1, WEAK_2], HA, short=True)
    assert reason == (
        "base forming — 2 weak HA candle(s) after strong 2026-07-01, "
        "waiting for a red HA candle"
    )
    # Once the candle is red (even weak) it is an entry, not a base.
    assert ha_base_forming([STRONG_GREEN, WEAK_1, WEAK_RED], HA, short=True) is None
    # Still strong → no base yet.
    assert ha_base_forming([STRONG_GREEN, Candle("b", 109, 120, 109, 119)], HA, short=True) is None
    # Weak candles with no strong run behind them are not a base.
    assert ha_base_forming([WEAK_1, WEAK_2], HA, short=True) is None


def test_rsi_tagged_returns_the_stretch_inside_the_window():
    assert rsi_tagged([65.0, 71.2, 68.0, None], threshold=70, above=True) == 71.2
    assert rsi_tagged([65.0, 69.9], threshold=70, above=True) is None
    assert rsi_tagged([35.0, 28.4, 26.1], threshold=30, above=False) == 26.1


def _normal_bars():
    return [
        bar(1, 100, 110, 99, 109),
        bar(2, 109, 114, 106, 111),
        bar(3, 111, 116, 108, 112),
        bar(4, 112, 118, 111, 117),  # strong green normal candle — no bearish shape
    ]


def test_ha_reversal_setup_default_ignores_the_normal_candle():
    ha = [STRONG_GREEN, WEAK_1, WEAK_2, SOLID_RED]
    setup = ha_reversal_setup(
        _normal_bars(), ha, [66.0, 72.5, 69.0, 64.0],
        call_threshold=70, put_threshold=30, ha_cfg=HA, candle_cfg=NORMAL,
    )
    assert setup is not None
    signal, label, stretch = setup
    assert signal is SignalType.HA_SHORT
    assert label == "HA strong 2026-07-01 → 2 weak → opposite body 75%"
    assert stretch == 72.5
    # No RSI tag in the window → no trade.
    assert (
        ha_reversal_setup(
            _normal_bars(), ha, [66.0, 68.5, 69.0, 64.0],
            call_threshold=70, put_threshold=30, ha_cfg=HA, candle_cfg=NORMAL,
        )
        is None
    )


def test_ha_reversal_setup_strict_requires_the_normal_candle_too():
    ha = [STRONG_GREEN, WEAK_1, WEAK_2, SOLID_RED]
    assert (
        ha_reversal_setup(
            _normal_bars(), ha, [66.0, 72.5, 69.0, 64.0],
            call_threshold=70, put_threshold=30, ha_cfg=STRICT, candle_cfg=NORMAL,
        )
        is None
    )
    bars = _normal_bars()[:-1] + [bar(4, 112, 113, 100, 101)]  # strong red body
    setup = ha_reversal_setup(
        bars, ha, [66.0, 72.5, 69.0, 64.0],
        call_threshold=70, put_threshold=30, ha_cfg=STRICT, candle_cfg=NORMAL,
    )
    assert setup is not None
    assert setup[1].startswith("strong red body + HA strong 2026-07-01")


def test_ha_reversal_setup_long_mirrors():
    ha = [
        Candle("a", 110, 110, 100, 101),  # strong red
        Candle("b", 102, 106, 98, 101),  # weak
        Candle("c", 100, 110, 99, 109),  # green
    ]
    bars = [bar(1, 110, 111, 100, 101), bar(2, 101, 105, 97, 100), bar(3, 100, 110, 99, 109)]
    setup = ha_reversal_setup(
        bars, ha, [33.0, 27.9, 31.0],
        call_threshold=70, put_threshold=30, ha_cfg=HA, candle_cfg=NORMAL,
    )
    assert setup is not None
    assert setup[0] is SignalType.HA_LONG
    assert setup[2] == 27.9


def test_ha_watch_setup_and_alert_are_not_tradeable():
    watch = ha_watch_setup(
        [STRONG_GREEN, WEAK_1, WEAK_2], [66.0, 72.5, 69.0],
        call_threshold=70, put_threshold=30, ha_cfg=HA,
    )
    assert watch is not None
    signal, reason, stretch = watch
    assert signal is SignalType.HA_SHORT and stretch == 72.5
    alert = make_ha_alert(
        symbol="BOSCHLTD", ltp=48_385.0, rsi=stretch, signal=signal, pattern="", skip_reason=reason
    )
    assert alert.skip_reason.startswith("base forming")
    assert alert.candle_pattern == ""
    assert "watch" in alert.message


def test_config_loads_the_heikin_ashi_book():
    config = load_config("config.yaml")
    assert config.heikin_ashi.rsi_lookback_sessions == 10
    assert config.heikin_ashi.min_weak_candles == 0
    assert config.heikin_ashi.opposite_needs_body is False
    assert config.heikin_ashi.require_normal_candle is False
    book = config.heikin_ashi_paper_trading
    assert book is not None and book.enabled
    assert book.name == "Heikin_Ashi"
    assert book.capital == 40_000_000
    assert book.lots_per_trade == 3
    assert book.stop_loss_pct == 1.5
    assert book.candle_stop is False
    assert book.ledger_path == "data/heikin_ashi_paper_book.json"
    assert book.google_worksheet == "Heikin Ashi Paper trades"
    assert book.google_summary_worksheet == "Heikin Ashi Portfolio Summary"
    assert len(config.candle_books()) == 3
