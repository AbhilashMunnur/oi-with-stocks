from datetime import date, timedelta

from src.price_extremes import extreme_entry_skip_reason


def _ohlc(days: int = 30, high: float = 100.0, low: float = 90.0, start: date | None = None):
    start = start or date(2026, 7, 1)
    rows = []
    for i in range(days):
        day = start + timedelta(days=i)
        rows.append((day.isoformat(), high, low, (high + low) / 2))
    return rows


def test_skips_within_two_percent_of_52_week_high():
    rows = _ohlc(30, high=100.0, low=80.0)
    reason = extreme_entry_skip_reason(
        98.5, rows, proximity_pct=2.0, today="2026-07-31"
    )
    assert reason is not None
    assert "high" in reason
    assert "1.50%" in reason


def test_allows_more_than_two_percent_from_high_and_low():
    rows = _ohlc(30, high=100.0, low=80.0)
    # 96 is 4% below 100 and 20% above 80.
    assert (
        extreme_entry_skip_reason(96.0, rows, proximity_pct=2.0, today="2026-07-31")
        is None
    )


def test_skips_within_two_percent_of_52_week_low():
    rows = _ohlc(30, high=120.0, low=100.0)
    reason = extreme_entry_skip_reason(
        101.5, rows, proximity_pct=2.0, today="2026-07-31"
    )
    assert reason is not None
    assert "low" in reason


def test_skips_for_two_sessions_after_high_cross_even_if_price_has_dumped():
    start = date(2026, 7, 1)
    rows = _ohlc(25, high=100.0, low=90.0, start=start)
    cross = start + timedelta(days=25)
    rows.append((cross.isoformat(), 110.0, 100.0, 108.0))
    d1 = cross + timedelta(days=1)
    d2 = cross + timedelta(days=2)
    rows.append((d1.isoformat(), 105.0, 99.0, 100.0))
    rows.append((d2.isoformat(), 102.0, 98.0, 100.0))
    reason = extreme_entry_skip_reason(
        100.0, rows, proximity_pct=2.0, cooldown_days=2, today=d2.isoformat()
    )
    assert reason is not None
    assert "crossed" in reason
    assert "2 sessions" in reason


def test_allows_entry_on_the_third_session_after_a_cross():
    start = date(2026, 7, 1)
    rows = _ohlc(25, high=100.0, low=90.0, start=start)
    cross = start + timedelta(days=25)
    rows.append((cross.isoformat(), 110.0, 100.0, 108.0))
    for offset in (1, 2, 3):
        day = cross + timedelta(days=offset)
        rows.append((day.isoformat(), 102.0, 98.0, 100.0))
    today = (cross + timedelta(days=3)).isoformat()
    assert (
        extreme_entry_skip_reason(
            100.0, rows, proximity_pct=2.0, cooldown_days=2, today=today
        )
        is None
    )


def test_missing_history_does_not_skip():
    assert extreme_entry_skip_reason(100.0, _ohlc(5), today="2026-07-06") is None


def test_combined_label_when_52_week_high_is_all_time_high():
    rows = _ohlc(30, high=100.0, low=80.0)
    reason = extreme_entry_skip_reason(99.0, rows, today="2026-07-31")
    assert reason is not None
    assert "52-week / all-time high" in reason
