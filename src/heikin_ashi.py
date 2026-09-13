"""Heikin_Ashi strategy: RSI 70/30 tag → strong HA run → (weak HA) → opposite colour.

Heikin-Ashi smooths each session into
    ha_close = (open + high + low + close) / 4
    ha_open  = (previous ha_open + previous ha_close) / 2
    ha_high  = max(high, ha_open, ha_close)
    ha_low   = min(low, ha_open, ha_close)
so a trend prints a run of one-colour candles with little wick against it.
Exhaustion shows as the bodies shrinking and wicks growing on both sides
("base forming" — reported, not traded); the first candle of the opposite
colour after the run is the entry. The normal OHLC chart supplies the RSI
and, only if configured, a confirming reversal candle.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.candle_patterns import Candle, day2_long_pattern, day2_short_pattern
from src.config import CandleConfig, HeikinAshiConfig, SignalType
from src.oi_analyzer import ScanAlert


def heikin_ashi(bars: list[Candle]) -> list[Candle]:
    """Heikin-Ashi series aligned 1:1 with ``bars`` (same dates)."""
    out: list[Candle] = []
    prev: Candle | None = None
    for bar in bars:
        ha_close = (bar.open + bar.high + bar.low + bar.close) / 4.0
        if prev is None:
            ha_open = (bar.open + bar.close) / 2.0
        else:
            ha_open = (prev.open + prev.close) / 2.0
        ha_high = max(bar.high, ha_open, ha_close)
        ha_low = min(bar.low, ha_open, ha_close)
        prev = Candle(bar.date, ha_open, ha_high, ha_low, ha_close)
        out.append(prev)
    return out


def is_green(bar: Candle) -> bool:
    return bar.close > bar.open


def is_red(bar: Candle) -> bool:
    return bar.close < bar.open


def is_weak(bar: Candle, cfg: HeikinAshiConfig) -> bool:
    """Small body, sticks either side — indecision inside the HA run."""
    return bar.span > 0 and bar.pct(bar.body) <= cfg.weak_body_pct


def is_strong(bar: Candle, cfg: HeikinAshiConfig, *, green: bool) -> bool:
    if bar.span <= 0:
        return False
    if green and not is_green(bar):
        return False
    if not green and not is_red(bar):
        return False
    return bar.pct(bar.body) >= cfg.strong_body_pct


@dataclass(frozen=True)
class HASequence:
    """Where the strong → weak → opposite run sits, for the alert text."""

    strong_date: str
    weak_count: int
    opposite_body_pct: float

    def describe(self) -> str:
        weak = f"{self.weak_count} weak → " if self.weak_count else ""
        return (
            f"HA strong {self.strong_date} → {weak}"
            f"opposite body {self.opposite_body_pct:.0f}%"
        )


def _weak_run_back_to_strong(
    ha: list[Candle], cfg: HeikinAshiConfig, *, short: bool, start: int
) -> tuple[int, int] | None:
    """From index ``start`` walk back over weak candles to a strong trend candle.

    Returns (index of the strong candle, weak count) or None.
    """
    weak = 0
    i = start
    while i >= 0 and is_weak(ha[i], cfg):
        weak += 1
        i -= 1
    if i < 0 or not is_strong(ha[i], cfg, green=short):
        return None
    return i, weak


def ha_sequence(
    ha: list[Candle], cfg: HeikinAshiConfig, *, short: bool
) -> HASequence | None:
    """Today (``ha[-1]``) is the opposite-colour candle closing the sequence.

    Walk back from yesterday over weak candles (any colour, at least
    ``min_weak_candles``); the candle before them must be a strong candle in
    the trend colour (green before a short, red before a long). Today may be
    any size unless ``opposite_needs_body`` is set.
    """
    if len(ha) < 2:
        return None
    today = ha[-1]
    if today.span <= 0:
        return None
    if short and not is_red(today):
        return None
    if not short and not is_green(today):
        return None
    if cfg.opposite_needs_body and is_weak(today, cfg):
        return None

    found = _weak_run_back_to_strong(ha, cfg, short=short, start=len(ha) - 2)
    if found is None:
        return None
    strong_i, weak = found
    if weak < cfg.min_weak_candles:
        return None
    return HASequence(
        strong_date=ha[strong_i].date,
        weak_count=weak,
        opposite_body_pct=today.pct(today.body),
    )


def ha_base_forming(
    ha: list[Candle], cfg: HeikinAshiConfig, *, short: bool
) -> str | None:
    """Watch state: strong trend run, then weak candles, no opposite colour yet.

    Today must itself be weak and still the trend colour (or a doji); the
    weak run walks back to a strong candle in the trend colour.
    """
    if len(ha) < 2:
        return None
    today = ha[-1]
    if today.span <= 0 or not is_weak(today, cfg):
        return None
    # A weak opposite-colour candle is already the entry, not the base.
    if short and is_red(today):
        return None
    if not short and is_green(today):
        return None
    found = _weak_run_back_to_strong(ha, cfg, short=short, start=len(ha) - 1)
    if found is None:
        return None
    strong_i, weak = found
    side = "red" if short else "green"
    return (
        f"base forming — {weak} weak HA candle(s) after strong {ha[strong_i].date}, "
        f"waiting for a {side} HA candle"
    )


def rsi_tagged(
    recent_rsi: list[float | None],
    *,
    threshold: float,
    above: bool,
) -> float | None:
    """Most stretched RSI in the window that crossed the level, else None."""
    hits = [
        v
        for v in recent_rsi
        if v is not None and ((v >= threshold) if above else (v <= threshold))
    ]
    if not hits:
        return None
    return max(hits) if above else min(hits)


def ha_reversal_setup(
    bars: list[Candle],
    ha: list[Candle],
    recent_rsi: list[float | None],
    *,
    call_threshold: float,
    put_threshold: float,
    ha_cfg: HeikinAshiConfig,
    candle_cfg: CandleConfig,
) -> tuple[SignalType, str, float] | None:
    """Return (signal, description, stretch RSI) when today completes a setup.

    ``bars[-1]`` / ``ha[-1]`` are today (live close folded in). ``recent_rsi``
    covers the lookback window and today's live RSI.
    """
    if not bars or len(bars) != len(ha):
        return None
    today = bars[-1]

    for short, threshold, above, signal, shape in (
        (True, call_threshold, True, SignalType.HA_SHORT, day2_short_pattern),
        (False, put_threshold, False, SignalType.HA_LONG, day2_long_pattern),
    ):
        stretch = rsi_tagged(recent_rsi, threshold=threshold, above=above)
        if stretch is None:
            continue
        seq = ha_sequence(ha, ha_cfg, short=short)
        if not seq:
            continue
        pattern = shape(today, candle_cfg)
        if ha_cfg.require_normal_candle and not pattern:
            continue
        label = f"{pattern} + {seq.describe()}" if pattern else seq.describe()
        return signal, label, stretch
    return None


def ha_watch_setup(
    ha: list[Candle],
    recent_rsi: list[float | None],
    *,
    call_threshold: float,
    put_threshold: float,
    ha_cfg: HeikinAshiConfig,
) -> tuple[SignalType, str, float] | None:
    """(signal, reason, stretch RSI) for a name in the weak-candle base."""
    for short, threshold, above, signal in (
        (True, call_threshold, True, SignalType.HA_SHORT),
        (False, put_threshold, False, SignalType.HA_LONG),
    ):
        stretch = rsi_tagged(recent_rsi, threshold=threshold, above=above)
        if stretch is None:
            continue
        reason = ha_base_forming(ha, ha_cfg, short=short)
        if reason:
            return signal, reason, stretch
    return None


def make_ha_alert(
    *,
    symbol: str,
    ltp: float,
    rsi: float,
    signal: SignalType,
    pattern: str,
    skip_reason: str | None = None,
) -> ScanAlert:
    status = f"not taking ({skip_reason})" if skip_reason else "taking"
    side = "short" if signal is SignalType.HA_SHORT else "long"
    shown = pattern or "watch"
    return ScanAlert(
        symbol=symbol,
        signal=signal,
        ltp=ltp,
        rsi=rsi,
        oi_strike=0.0,
        oi_value=0,
        distance_pct=0.0,
        expiry="",
        candle_pattern=pattern,
        skip_reason=skip_reason,
        stop_price=None,
        message=f"{symbol}: Heikin_Ashi {side} (stretch RSI {rsi:.1f}) {shown} — {status}",
    )
