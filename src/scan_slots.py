from __future__ import annotations

from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
FIRST_SLOT = time(9, 30)
LAST_SLOT = time(15, 30)
S1_WALL_EXIT_SLOT = time(15, 15)
# F&O close is 15:40 IST; 15:45 marks books to the closing print.
CLOSE_PNL_SLOT = time(15, 45)
SESSION_END = time(16, 0)
CASH_CLOSE = time(15, 30)
DEFAULT_MARKER = Path("data/last_scan_slot.txt")
# Start the GitHub job this many seconds before the slot so pip / login / the
# scrip master are done by :00/:30. Telegram should then land within ~5 minutes.
WARMUP_SECONDS = 10 * 60


def now_ist(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(IST)
    if now.tzinfo is None:
        return now.replace(tzinfo=IST)
    return now.astimezone(IST)


def active_slot(now: datetime | None = None) -> datetime | None:
    """Current scan slot in IST, or None outside 09:30–15:45.

    Half-hour slots, 15:15 (S1 wall-break), and 15:45 (closing P&L after
    F&O ends at 15:40). A late cron still belongs to the slot that has
    already started — at 09:45 that is 09:30; at 15:50, 15:45.
    """
    current = now_ist(now)
    if current.weekday() >= 5:
        return None

    clock = current.time()
    if CLOSE_PNL_SLOT <= clock < SESSION_END:
        return current.replace(hour=15, minute=45, second=0, microsecond=0)

    if S1_WALL_EXIT_SLOT <= clock < LAST_SLOT:
        return current.replace(hour=15, minute=15, second=0, microsecond=0)

    minute = 0 if current.minute < 30 else 30
    slot = current.replace(minute=minute, second=0, microsecond=0)
    if slot.time() < FIRST_SLOT or slot.time() > LAST_SLOT:
        return None
    return slot


def is_s1_wall_exit_slot(now: datetime | None = None) -> bool:
    """True from the 15:15 IST scan onward (15:30/15:45 are backups if 15:15 was missed)."""
    current = now_ist(now)
    slot = active_slot(now)
    return slot is not None and current.time() >= S1_WALL_EXIT_SLOT


def is_close_pnl_slot(now: datetime | None = None) -> bool:
    """True for the 15:45 IST closing P&L mark after F&O 15:40."""
    slot = active_slot(now)
    return slot is not None and slot.time() == CLOSE_PNL_SLOT


def is_cash_stop_slot(now: datetime | None = None) -> bool:
    """True at 15:30 (cash close) and 15:45 (backup after F&O ends).

    Candle stops wait for a cash close through the stored bar. A 15:15 wick
    or an earlier 30-minute futures print is not a stop.
    """
    slot = active_slot(now)
    return slot is not None and slot.time() in (CASH_CLOSE, CLOSE_PNL_SLOT)


def is_candle_entry_window(now: datetime | None = None) -> bool:
    """RSI_CandlePattern takes from 15:15 IST on a weekday — not in the morning.

    F&O is live at 15:15; cash close is not required. After 15:15 it stays
    valid through the rest of that calendar day (15:30 is a backup).
    """
    current = now_ist(now)
    if current.weekday() >= 5:
        return False
    return current.time() >= S1_WALL_EXIT_SLOT


def is_same_day_reversal_window(now: datetime | None = None) -> bool:
    """Same clock as candle entries: 15:15 IST, not cash close."""
    return is_candle_entry_window(now)


def read_last_slot(path: Path = DEFAULT_MARKER) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def write_last_slot(slot: datetime, path: Path = DEFAULT_MARKER) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(slot.isoformat(), encoding="utf-8")


def target_scan_slot(
    *,
    now: datetime | None = None,
    path: Path = DEFAULT_MARKER,
    warmup_seconds: int = WARMUP_SECONDS,
) -> datetime | None:
    """Unpaid slot this run should serve, including the next one during warmup.

    An unpaid current slot always wins (a late 09:30 still runs at 09:56).
    Otherwise, if the next slot is within `warmup_seconds`, return that so the
    runner can install deps and log in before the clock hits the slot.
    """
    current = now_ist(now)
    due = active_slot(current)
    if due is not None and read_last_slot(path) != due.isoformat():
        return due

    for slot in iter_slots_for_day(current):
        if slot <= current:
            continue
        if (slot - current).total_seconds() <= warmup_seconds:
            if read_last_slot(path) != slot.isoformat():
                return slot
        break
    return None


def should_run_slot(
    *,
    force: bool = False,
    now: datetime | None = None,
    path: Path = DEFAULT_MARKER,
) -> tuple[bool, str, datetime | None]:
    """Return (run, reason, slot)."""
    if force:
        return True, "forced", active_slot(now)

    slot = target_scan_slot(now=now, path=path)
    if slot is None:
        current = active_slot(now)
        if current is not None and read_last_slot(path) == current.isoformat():
            return False, f"slot {current:%H:%M} IST already completed", current
        return False, "outside 09:30–15:45 IST scan slots", None

    current = now_ist(now)
    if slot > current:
        return True, f"warmup for slot {slot:%H:%M} IST", slot
    return True, f"due for slot {slot:%H:%M} IST", slot


def iter_slots_for_day(day: datetime) -> list[datetime]:
    """Half-hour slots 09:30–15:30 IST, plus 15:15 wall-break and 15:45 close."""
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
    close_pnl = day.replace(hour=15, minute=45, second=0, microsecond=0)
    slots.append(wall_exit)
    slots.append(close_pnl)
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


def seconds_until_warmup_dispatch(
    now: datetime | None = None,
    warmup_seconds: int = WARMUP_SECONDS,
) -> int | None:
    """Seconds to wait before dispatching the next slot's warmup job."""
    wait = seconds_until_next_slot(now)
    if wait is None:
        return None
    return max(1, wait - warmup_seconds)
