from __future__ import annotations

from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
FIRST_SLOT = time(9, 30)
LAST_SLOT = time(15, 30)
S1_WALL_EXIT_SLOT = time(15, 15)
DEFAULT_MARKER = Path("data/last_scan_slot.txt")


def now_ist(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(IST)
    if now.tzinfo is None:
        return now.replace(tzinfo=IST)
    return now.astimezone(IST)


def active_slot(now: datetime | None = None) -> datetime | None:
    """Current scan slot in IST, or None outside 09:30–15:30.

    Half-hour slots plus 15:15 (S1 wall-break). A late cron still belongs to
    the slot that has already started — at 09:45 that is 09:30; at 15:20, 15:15.
    """
    current = now_ist(now)
    if current.weekday() >= 5:
        return None

    if S1_WALL_EXIT_SLOT <= current.time() < LAST_SLOT:
        return current.replace(hour=15, minute=15, second=0, microsecond=0)

    minute = 0 if current.minute < 30 else 30
    slot = current.replace(minute=minute, second=0, microsecond=0)
    if slot.time() < FIRST_SLOT or slot.time() > LAST_SLOT:
        return None
    return slot


def is_s1_wall_exit_slot(now: datetime | None = None) -> bool:
    """True from the 15:15 IST scan onward (15:30 is a backup if 15:15 was missed)."""
    current = now_ist(now)
    slot = active_slot(now)
    return slot is not None and current.time() >= S1_WALL_EXIT_SLOT


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


def iter_slots_for_day(day: datetime) -> list[datetime]:
    """Half-hour slots 09:30–15:30 IST, plus 15:15 for the S1 wall-break check."""
    day = now_ist(day)
    start = day.replace(hour=9, minute=30, second=0, microsecond=0)
    slots: list[datetime] = []
    cursor = start
    while cursor.time() <= LAST_SLOT:
        slots.append(cursor)
        if cursor.minute == 0:
            cursor = cursor.replace(minute=30)
        else:
            cursor = cursor.replace(hour=cursor.hour + 1, minute=0)
    wall_exit = day.replace(hour=15, minute=15, second=0, microsecond=0)
    slots.append(wall_exit)
    slots.sort()
    return slots


def seconds_until_next_slot(now: datetime | None = None) -> int | None:
    """Seconds until the next scan slot starts today, or None if none left.

    Used by GitHub Actions to self-chain half-hour runs when cron goes quiet.
    """
    current = now_ist(now)
    if current.weekday() >= 5:
        return None

    for slot in iter_slots_for_day(current):
        if slot > current:
            return max(1, int((slot - current).total_seconds()))
    return None
