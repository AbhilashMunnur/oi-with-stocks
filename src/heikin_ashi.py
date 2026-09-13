"""Heikin_Ashi strategy: RSI 70/30 tag → strong HA → weak HA → opposite colour.

Heikin-Ashi smooths each session into
    ha_close = (open + high + low + close) / 4
    ha_open  = (previous ha_open + previous ha_close) / 2
    ha_high  = max(high, ha_open, ha_close)
    ha_low   = min(low, ha_open, ha_close)
so a trend prints a run of one-colour candles with little wick against it.
Exhaustion shows as the bodies shrinking and wicks growing on both sides;
the first solid candle of the opposite colour after that run is the entry,
provided the normal candle that day is also one of the reversal shapes.
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
        return (
            f"HA strong {self.strong_date} → {self.weak_count} weak → "
            f"opposite body {self.opposite_body_pct:.0f}%"
        )


def ha_sequence(
    ha: list[Candle], cfg: HeikinAshiConfig, *, short: bool
) -> HASequence | None:
    """Today (``ha[-1]``) is the opposite-colour candle closing the sequence.

    Walk back from yesterday over weak candles (any colour); the first
    non-weak candle before them must be a strong candle in the trend colour
    (green before a short, red before a long). Today must not itself be weak.
    """
    if len(ha) < 3:
        return None
    today = ha[-1]
    if today.span <= 0 or is_weak(today, cfg):
        return None
    if short and not is_red(today):
        return None
    if not short and not is_green(today):
        return None

    weak = 0
    i = len(ha) - 2
    while i >= 0 and is_weak(ha[i], cfg):
        weak += 1
        i -= 1
    if weak < cfg.min_weak_candles or i < 0:
        return None
    if not is_strong(ha[i], cfg, green=short):
        return None
    return HASequence(
        strong_date=ha[i].date,
        weak_count=weak,
        opposite_body_pct=today.pct(today.body),
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

    stretch = rsi_tagged(recent_rsi, threshold=call_threshold, above=True)
    if stretch is not None:
        seq = ha_sequence(ha, ha_cfg, short=True)
        pattern = day2_short_pattern(today, candle_cfg) if seq else None
        if seq and pattern:
            return SignalType.HA_SHORT, f"{pattern} + {seq.describe()}", stretch

    stretch = rsi_tagged(recent_rsi, threshold=put_threshold, above=False)
    if stretch is not None:
        seq = ha_sequence(ha, ha_cfg, short=False)
        pattern = day2_long_pattern(today, candle_cfg) if seq else None
        if seq and pattern:
            return SignalType.HA_LONG, f"{pattern} + {seq.describe()}", stretch
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
        message=f"{symbol}: Heikin_Ashi {side} (stretch RSI {rsi:.1f}) {pattern} — {status}",
    )
