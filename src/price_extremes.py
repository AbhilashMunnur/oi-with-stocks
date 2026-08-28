from __future__ import annotations

from datetime import date

WEEK52_BARS = 252
MIN_BARS = 20

OHLC = tuple[str, float, float, float]  # date, high, low, close


def _window(rows: list[OHLC]) -> list[OHLC]:
    return rows[-WEEK52_BARS:] if len(rows) >= WEEK52_BARS else rows


def _levels(rows: list[OHLC]) -> tuple[float, float, float, float]:
    """52-week high/low and all-time high/low from completed bars."""
    week = _window(rows)
    week52_high = max(high for _, high, _low, _close in week)
    week52_low = min(low for _, _high, low, _close in week)
    all_time_high = max(high for _, high, _low, _close in rows)
    all_time_low = min(low for _, _high, low, _close in rows)
    return week52_high, week52_low, all_time_high, all_time_low


def _same(left: float, right: float) -> bool:
    if left <= 0 or right <= 0:
        return False
    return abs(left - right) / max(left, right) < 0.0005


def _high_label(week52_high: float, all_time_high: float, crossed: float) -> str:
    hit_ath = crossed > all_time_high or _same(crossed, all_time_high)
    hit_w52 = crossed > week52_high or _same(crossed, week52_high)
    if hit_ath and (hit_w52 or _same(week52_high, all_time_high)):
        return "52-week / all-time high"
    if hit_ath:
        return "all-time high"
    return "52-week high"


def _low_label(week52_low: float, all_time_low: float, crossed: float) -> str:
    hit_atl = crossed < all_time_low or _same(crossed, all_time_low)
    hit_w52 = crossed < week52_low or _same(crossed, week52_low)
    if hit_atl and (hit_w52 or _same(week52_low, all_time_low)):
        return "52-week / all-time low"
    if hit_atl:
        return "all-time low"
    return "52-week low"


def _with_live_bar(ohlc: list[OHLC], today: str, ltp: float) -> list[OHLC]:
    rows = list(ohlc)
    if rows and rows[-1][0] == today:
        _date, high, low, _close = rows[-1]
        rows[-1] = (today, max(high, ltp), min(low, ltp), ltp)
        return rows
    rows.append((today, ltp, ltp, ltp))
    return rows


def _last_cross(
    ohlc: list[OHLC],
    *,
    high_side: bool,
) -> tuple[str, str] | None:
    """Most recent session that took out the prior 52-week or all-time extreme."""
    found: tuple[str, str] | None = None
    for index in range(MIN_BARS, len(ohlc)):
        prior = ohlc[:index]
        day, high, low, _close = ohlc[index]
        week52_high, week52_low, all_time_high, all_time_low = _levels(prior)
        if high_side:
            if high > all_time_high:
                found = (day, _high_label(week52_high, all_time_high, high))
            elif high > week52_high:
                found = (day, _high_label(week52_high, all_time_high, high))
        else:
            if low < all_time_low:
                found = (day, _low_label(week52_low, all_time_low, low))
            elif low < week52_low:
                found = (day, _low_label(week52_low, all_time_low, low))
    return found


def _sessions_since(cross_date: str, ohlc: list[OHLC], today: str) -> int:
    later = [day for day, _h, _l, _c in ohlc if day > cross_date]
    if today > cross_date and today not in later:
        later.append(today)
    return len(later)


def _proximity_skip(
    ltp: float,
    level: float,
    label: str,
    proximity_pct: float,
) -> str | None:
    if level <= 0 or ltp <= 0:
        return None
    distance = abs(ltp - level) / level * 100
    if distance > proximity_pct:
        return None
    return f"within {distance:.2f}% of {label} ₹{level:,.2f} (need > {proximity_pct:g}%)"


def extreme_entry_skip_reason(
    ltp: float,
    ohlc: list[OHLC],
    *,
    proximity_pct: float = 2.0,
    cooldown_days: int = 2,
    today: date | str | None = None,
) -> str | None:
    """Skip new entries near 52-week / all-time highs and lows.

    Also skip for ``cooldown_days`` sessions after price crosses those
    levels (cross day counts as session 0). Missing history does not skip.
    """
    if proximity_pct <= 0 or ltp <= 0:
        return None
    today_s = (
        today
        if isinstance(today, str)
        else (today or date.today()).isoformat()
    )
    if len(ohlc) < MIN_BARS:
        return None

    live = _with_live_bar(ohlc, today_s, ltp)
    prior = [row for row in live if row[0] < today_s]
    if len(prior) < MIN_BARS:
        return None

    week52_high, week52_low, all_time_high, all_time_low = _levels(prior)
    today_high = max(high for day, high, _l, _c in live if day == today_s)
    today_low = min(low for day, _h, low, _c in live if day == today_s)
    cur_w52h = max(week52_high, today_high)
    cur_w52l = min(week52_low, today_low)
    cur_ath = max(all_time_high, today_high)
    cur_atl = min(all_time_low, today_low)

    high_name = (
        "52-week / all-time high"
        if _same(cur_w52h, cur_ath)
        else "all-time high"
    )
    w52_high_name = "52-week high" if not _same(cur_w52h, cur_ath) else high_name
    low_name = (
        "52-week / all-time low"
        if _same(cur_w52l, cur_atl)
        else "all-time low"
    )
    w52_low_name = "52-week low" if not _same(cur_w52l, cur_atl) else low_name

    for level, label in (
        (cur_ath, high_name),
        (cur_w52h, w52_high_name),
        (cur_atl, low_name),
        (cur_w52l, w52_low_name),
    ):
        reason = _proximity_skip(ltp, level, label, proximity_pct)
        if reason:
            return reason

    high_cross = _last_cross(live, high_side=True)
    if high_cross and _sessions_since(high_cross[0], live, today_s) <= cooldown_days:
        return (
            f"{high_cross[1]} crossed {high_cross[0]} "
            f"— no new trades for {cooldown_days} sessions"
        )
    low_cross = _last_cross(live, high_side=False)
    if low_cross and _sessions_since(low_cross[0], live, today_s) <= cooldown_days:
        return (
            f"{low_cross[1]} crossed {low_cross[0]} "
            f"— no new trades for {cooldown_days} sessions"
        )
    return None
