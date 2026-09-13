from src.candle_patterns import Candle
from src.config import CandleConfig, HeikinAshiConfig, SignalType, load_config
from src.heikin_ashi import (
    ha_reversal_setup,
    ha_sequence,
    heikin_ashi,
    is_strong,
    is_weak,
    rsi_tagged,
)

HA = HeikinAshiConfig()
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


def _short_sequence_ha():
    """Strong green → two weak → solid red (already Heikin-Ashi values)."""
    return [
        Candle("2026-07-01", 100, 110, 100, 109),
        Candle("2026-07-02", 108, 114, 104, 110),  # weak, body 20%
        Candle("2026-07-03", 110, 115, 105, 111),  # weak, body 10%
        Candle("2026-07-04", 111, 112, 100, 102),  # red, body 75%
    ]


def test_ha_sequence_short_needs_strong_then_weak_then_red():
    seq = ha_sequence(_short_sequence_ha(), HA, short=True)
    assert seq is not None
    assert seq.strong_date == "2026-07-01"
    assert seq.weak_count == 2
    # Same candles do not qualify as a long.
    assert ha_sequence(_short_sequence_ha(), HA, short=False) is None


def test_ha_sequence_rejects_missing_weak_run_or_weak_opposite():
    no_weak = [
        Candle("a", 100, 110, 100, 109),
        Candle("b", 109, 112, 100, 102),
    ]
    assert ha_sequence(no_weak, HA, short=True) is None

    weak_red_today = _short_sequence_ha()[:-1] + [Candle("d", 111, 115, 105, 110)]
    assert ha_sequence(weak_red_today, HA, short=True) is None

    stricter = HeikinAshiConfig(min_weak_candles=3)
    assert ha_sequence(_short_sequence_ha(), stricter, short=True) is None


def test_rsi_tagged_returns_the_stretch_inside_the_window():
    assert rsi_tagged([65.0, 71.2, 68.0, None], threshold=70, above=True) == 71.2
    assert rsi_tagged([65.0, 69.9], threshold=70, above=True) is None
    assert rsi_tagged([35.0, 28.4, 26.1], threshold=30, above=False) == 26.1


def test_ha_reversal_setup_short_requires_normal_reversal_candle_too():
    ha = _short_sequence_ha()
    # Normal bars: last one is a strong red body (a bearish reversal shape).
    bars = [
        bar(1, 100, 110, 99, 109),
        bar(2, 109, 114, 106, 111),
        bar(3, 111, 116, 108, 112),
        bar(4, 112, 113, 100, 101),
    ]
    setup = ha_reversal_setup(
        bars, ha, [66.0, 72.5, 69.0, 64.0],
        call_threshold=70, put_threshold=30, ha_cfg=HA, candle_cfg=NORMAL,
    )
    assert setup is not None
    signal, pattern, stretch = setup
    assert signal is SignalType.HA_SHORT
    assert pattern.startswith("strong red body + HA strong 2026-07-01")
    assert stretch == 72.5

    # Same HA picture but a strong green normal candle → no short.
    bars_green = bars[:-1] + [bar(4, 100, 113, 99, 112)]
    assert (
        ha_reversal_setup(
            bars_green, ha, [66.0, 72.5, 69.0, 64.0],
            call_threshold=70, put_threshold=30, ha_cfg=HA, candle_cfg=NORMAL,
        )
        is None
    )
    # No RSI tag in the window → no trade.
    assert (
        ha_reversal_setup(
            bars, ha, [66.0, 68.5, 69.0, 64.0],
            call_threshold=70, put_threshold=30, ha_cfg=HA, candle_cfg=NORMAL,
        )
        is None
    )


def test_ha_reversal_setup_long_mirrors():
    ha = [
        Candle("a", 110, 110, 100, 101),  # strong red
        Candle("b", 102, 106, 98, 101),  # weak
        Candle("c", 100, 101, 90, 108) if False else Candle("c", 100, 110, 99, 109),  # solid green
    ]
    bars = [
        bar(1, 110, 111, 100, 101),
        bar(2, 101, 105, 97, 100),
        bar(3, 100, 110, 99, 109),  # strong green body
    ]
    setup = ha_reversal_setup(
        bars, ha, [33.0, 27.9, 31.0],
        call_threshold=70, put_threshold=30, ha_cfg=HA, candle_cfg=NORMAL,
    )
    assert setup is not None
    assert setup[0] is SignalType.HA_LONG
    assert setup[2] == 27.9


def test_config_loads_the_heikin_ashi_book():
    config = load_config("config.yaml")
    assert config.heikin_ashi.rsi_lookback_sessions == 10
    assert config.heikin_ashi.min_weak_candles == 1
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
