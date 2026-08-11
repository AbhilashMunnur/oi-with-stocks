from datetime import datetime
from zoneinfo import ZoneInfo

from src.scan_slots import active_slot, should_run_slot, write_last_slot

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
