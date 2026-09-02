from datetime import datetime
from zoneinfo import ZoneInfo

from src.scan_slots import (
    active_slot,
    is_s1_wall_exit_slot,
    is_candle_entry_window,
    is_same_day_reversal_window,
    iter_slots_for_day,
    seconds_until_next_slot,
    seconds_until_warmup_dispatch,
    should_run_slot,
    write_last_slot,
)

IST = ZoneInfo("Asia/Kolkata")


def test_first_slot_is_nine_thirty():
    assert active_slot(datetime(2026, 8, 11, 9, 30, tzinfo=IST)).strftime("%H:%M") == "09:30"


def test_late_cron_still_belongs_to_prior_half_hour():
    assert active_slot(datetime(2026, 8, 11, 9, 45, tzinfo=IST)).strftime("%H:%M") == "09:30"
    assert active_slot(datetime(2026, 8, 11, 10, 5, tzinfo=IST)).strftime("%H:%M") == "10:00"


def test_before_nine_thirty_is_closed():
    assert active_slot(datetime(2026, 8, 11, 9, 20, tzinfo=IST)) is None


def test_weekend_is_closed():
    assert active_slot(datetime(2026, 8, 15, 10, 0, tzinfo=IST)) is None  # Saturday


def test_slot_guard_skips_duplicate(tmp_path):
    marker = tmp_path / "last_scan_slot.txt"
    now = datetime(2026, 8, 11, 9, 40, tzinfo=IST)
    slot = active_slot(now)
    write_last_slot(slot, marker)

    run, reason, _ = should_run_slot(now=now, path=marker)
    assert run is False
    assert "already completed" in reason


def test_slot_guard_runs_when_due(tmp_path):
    marker = tmp_path / "last_scan_slot.txt"
    now = datetime(2026, 8, 11, 10, 2, tzinfo=IST)

    run, reason, slot = should_run_slot(now=now, path=marker)
    assert run is True
    assert slot.strftime("%H:%M") == "10:00"
    assert "due" in reason


def test_day_includes_three_fifteen_wall_exit_slot():
    day = datetime(2026, 8, 11, 12, 0, tzinfo=IST)
    slots = iter_slots_for_day(day)
    stamps = [slot.strftime("%H:%M") for slot in slots]
    assert stamps[0] == "09:30"
    assert "15:15" in stamps
    assert stamps[-1] == "15:45"
    assert stamps[-2] == "15:30"
    assert "15:15" in stamps


def test_three_fifteen_is_its_own_slot():
    assert active_slot(datetime(2026, 8, 11, 15, 14, tzinfo=IST)).strftime("%H:%M") == "15:00"
    assert active_slot(datetime(2026, 8, 11, 15, 15, tzinfo=IST)).strftime("%H:%M") == "15:15"
    assert active_slot(datetime(2026, 8, 11, 15, 20, tzinfo=IST)).strftime("%H:%M") == "15:15"
    assert active_slot(datetime(2026, 8, 11, 15, 30, tzinfo=IST)).strftime("%H:%M") == "15:30"


def test_seconds_until_next_slot_after_a_scan():
    now = datetime(2026, 8, 11, 14, 2, tzinfo=IST)
    wait = seconds_until_next_slot(now)
    assert wait == 28 * 60


def test_seconds_until_three_fifteen_after_three_oclock():
    now = datetime(2026, 8, 11, 15, 2, tzinfo=IST)
    wait = seconds_until_next_slot(now)
    assert wait == 13 * 60


def test_fifteen_slot_still_runs_after_three_oclock_completed(tmp_path):
    marker = tmp_path / "last_scan_slot.txt"
    three_oclock = active_slot(datetime(2026, 8, 11, 15, 2, tzinfo=IST))
    write_last_slot(three_oclock, marker)

    run, reason, slot = should_run_slot(
        now=datetime(2026, 8, 11, 15, 16, tzinfo=IST), path=marker
    )
    assert run is True
    assert slot.strftime("%H:%M") == "15:15"
    assert "due" in reason


def test_seconds_until_next_slot_none_after_last():
    now = datetime(2026, 8, 11, 15, 50, tzinfo=IST)
    assert seconds_until_next_slot(now) is None


def test_seconds_until_close_slot_after_three_thirty():
    now = datetime(2026, 8, 11, 15, 32, tzinfo=IST)
    assert seconds_until_next_slot(now) == 13 * 60


def test_close_pnl_slot_is_three_forty_five():
    from src.scan_slots import is_close_pnl_slot

    assert active_slot(datetime(2026, 8, 11, 15, 40, tzinfo=IST)).strftime("%H:%M") == "15:30"
    assert active_slot(datetime(2026, 8, 11, 15, 45, tzinfo=IST)).strftime("%H:%M") == "15:45"
    assert active_slot(datetime(2026, 8, 11, 15, 50, tzinfo=IST)).strftime("%H:%M") == "15:45"
    assert active_slot(datetime(2026, 8, 11, 16, 0, tzinfo=IST)) is None
    assert is_close_pnl_slot(datetime(2026, 8, 11, 15, 30, tzinfo=IST)) is False
    assert is_close_pnl_slot(datetime(2026, 8, 11, 15, 45, tzinfo=IST)) is True


def test_cash_stop_slot_is_cash_close_and_backup():
    from src.scan_slots import is_cash_stop_slot

    assert is_cash_stop_slot(datetime(2026, 8, 11, 15, 15, tzinfo=IST)) is False
    assert is_cash_stop_slot(datetime(2026, 8, 11, 15, 30, tzinfo=IST)) is True
    assert is_cash_stop_slot(datetime(2026, 8, 11, 15, 45, tzinfo=IST)) is True
    assert is_cash_stop_slot(datetime(2026, 8, 11, 14, 30, tzinfo=IST)) is False


def test_ten_minute_warmup_starts_at_ten_fifty(tmp_path):
    marker = tmp_path / "last_scan_slot.txt"
    write_last_slot(
        active_slot(datetime(2026, 8, 11, 10, 35, tzinfo=IST)), marker
    )
    run, reason, slot = should_run_slot(
        now=datetime(2026, 8, 11, 10, 50, tzinfo=IST), path=marker
    )
    assert run is True
    assert slot.strftime("%H:%M") == "11:00"
    assert "warmup" in reason


def test_warmup_inside_ten_minute_window(tmp_path):
    marker = tmp_path / "last_scan_slot.txt"
    write_last_slot(
        active_slot(datetime(2026, 8, 11, 9, 35, tzinfo=IST)), marker
    )
    run, reason, slot = should_run_slot(
        now=datetime(2026, 8, 11, 9, 56, tzinfo=IST), path=marker
    )
    assert run is True
    assert slot.strftime("%H:%M") == "10:00"
    assert "warmup" in reason


def test_too_early_for_warmup_is_skipped(tmp_path):
    marker = tmp_path / "last_scan_slot.txt"
    write_last_slot(
        active_slot(datetime(2026, 8, 11, 9, 35, tzinfo=IST)), marker
    )
    run, reason, _ = should_run_slot(
        now=datetime(2026, 8, 11, 9, 45, tzinfo=IST), path=marker
    )
    assert run is False
    assert "already completed" in reason


def test_open_warmup_before_nine_thirty(tmp_path):
    marker = tmp_path / "last_scan_slot.txt"
    run, reason, slot = should_run_slot(
        now=datetime(2026, 8, 11, 9, 26, tzinfo=IST), path=marker
    )
    assert run is True
    assert slot.strftime("%H:%M") == "09:30"
    assert "warmup" in reason


def test_unpaid_current_slot_beats_next_warmup(tmp_path):
    marker = tmp_path / "last_scan_slot.txt"
    run, _, slot = should_run_slot(
        now=datetime(2026, 8, 11, 9, 56, tzinfo=IST), path=marker
    )
    assert run is True
    assert slot.strftime("%H:%M") == "09:30"


def test_seconds_until_warmup_dispatch():
    now = datetime(2026, 8, 11, 10, 4, tzinfo=IST)
    assert seconds_until_warmup_dispatch(now) == 16 * 60


def test_s1_wall_exit_starts_at_three_fifteen():
    assert is_s1_wall_exit_slot(datetime(2026, 8, 11, 15, 0, tzinfo=IST)) is False
    assert is_s1_wall_exit_slot(datetime(2026, 8, 11, 15, 14, tzinfo=IST)) is False
    assert is_s1_wall_exit_slot(datetime(2026, 8, 11, 15, 15, tzinfo=IST)) is True
    assert is_s1_wall_exit_slot(datetime(2026, 8, 11, 15, 30, tzinfo=IST)) is True
    assert is_s1_wall_exit_slot(datetime(2026, 8, 11, 12, 5, tzinfo=IST)) is False
    assert is_s1_wall_exit_slot(datetime(2026, 8, 11, 16, 0, tzinfo=IST)) is False


def test_candle_entries_from_three_fifteen():
    assert is_candle_entry_window(datetime(2026, 8, 11, 15, 14, tzinfo=IST)) is False
    assert is_candle_entry_window(datetime(2026, 8, 11, 15, 15, tzinfo=IST)) is True
    assert is_same_day_reversal_window(datetime(2026, 8, 11, 15, 15, tzinfo=IST)) is True
    assert is_candle_entry_window(datetime(2026, 8, 11, 22, 0, tzinfo=IST)) is True
    assert is_candle_entry_window(datetime(2026, 8, 15, 15, 15, tzinfo=IST)) is False
