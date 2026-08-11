from __future__ import annotations

from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
FIRST_SLOT = time(9, 30)
LAST_SLOT = time(15, 30)
DEFAULT_MARKER = Path("data/last_scan_slot.txt")


def now_ist(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(IST)
    if now.tzinfo is None:
        return now.replace(tzinfo=IST)
    return now.astimezone(IST)


def active_slot(now: datetime | None = None) -> datetime | None:
    """Current 30-minute scan slot in IST, or None outside 09:30–15:30.

    At 09:45 the active slot is still 09:30, so a late GitHub cron can catch up.
    """
    current = now_ist(now)
    if current.weekday() >= 5:
        return None

    minute = 0 if current.minute < 30 else 30
    slot = current.replace(minute=minute, second=0, microsecond=0)
    if slot.time() < FIRST_SLOT or slot.time() > LAST_SLOT:
        return None
    return slot


def read_last_slot(path: Path = DEFAULT_MARKER) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def write_last_slot(slot: datetime, path: Path = DEFAULT_MARKER) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(slot.isoformat(), encoding="utf-8")


def should_run_slot(
    *,
    force: bool = False,
    now: datetime | None = None,
    path: Path = DEFAULT_MARKER,
) -> tuple[bool, str, datetime | None]:
    """Return (run, reason, slot)."""
    if force:
        return True, "forced", active_slot(now)

    slot = active_slot(now)
    if slot is None:
        return False, "outside 09:30–15:30 IST scan slots", None

    marker = slot.isoformat()
    if read_last_slot(path) == marker:
        return False, f"slot {slot:%H:%M} IST already completed", slot

    return True, f"due for slot {slot:%H:%M} IST", slot
