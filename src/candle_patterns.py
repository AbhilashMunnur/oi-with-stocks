"""RSI + candle reversal: do not fade a 70/30 stretch until a day-2 reversal prints."""

from __future__ import annotations

from dataclasses import dataclass

from src.config import CandleConfig, SignalType
from src.oi_analyzer import ScanAlert


@dataclass(frozen=True)
class Candle:
    date: str
    open: float
    high: float
    low: float
    close: float

    @property
    def span(self) -> float:
        return self.high - self.low

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    def pct(self, part: float) -> float:
        if self.span <= 0:
            return 0.0
        return part / self.span * 100.0


def with_live_close(bar: Candle, ltp: float) -> Candle:
    """Today's still-forming bar: keep the open, stretch high/low with LTP."""
    if ltp <= 0:
        return bar
    return Candle(
        date=bar.date,
        open=bar.open,
        high=max(bar.high, ltp),
        low=min(bar.low, ltp) if bar.low > 0 else ltp,
        close=ltp,
    )


def is_strong_bull(bar: Candle, cfg: CandleConfig) -> bool:
    return bar.close > bar.open and bar.pct(bar.body) >= cfg.strong_body_pct


def is_strong_bear(bar: Candle, cfg: CandleConfig) -> bool:
    return bar.close < bar.open and bar.pct(bar.body) >= cfg.strong_body_pct


def is_inverted_hammer(bar: Candle, cfg: CandleConfig) -> bool:
    """Small body near the low, long upper stick, little stick below."""
    if bar.span <= 0:
        return False
    if bar.pct(bar.body) > cfg.weak_body_pct:
        return False
    if bar.pct(bar.upper_wick) < cfg.hammer_long_wick_pct:
        return False
    if bar.pct(bar.lower_wick) > cfg.hammer_short_wick_pct:
        return False
    return max(bar.open, bar.close) <= bar.low + 0.5 * bar.span


def is_hammer(bar: Candle, cfg: CandleConfig) -> bool:
    """Small body near the high, long lower stick — inverse inverted hammer."""
    if bar.span <= 0:
        return False
    if bar.pct(bar.body) > cfg.weak_body_pct:
        return False
    if bar.pct(bar.lower_wick) < cfg.hammer_long_wick_pct:
        return False
    if bar.pct(bar.upper_wick) > cfg.hammer_short_wick_pct:
        return False
    return min(bar.open, bar.close) >= bar.high - 0.5 * bar.span


def is_weak_middle(bar: Candle, cfg: CandleConfig) -> bool:
    """Body in the middle, stick above and below, body ≤ 40%."""
    if bar.span <= 0:
        return False
    if bar.pct(bar.body) > cfg.weak_body_pct:
        return False
    if bar.pct(bar.upper_wick) < cfg.side_wick_pct:
        return False
    if bar.pct(bar.lower_wick) < cfg.side_wick_pct:
        return False
    return True


def day2_short_pattern(bar: Candle, cfg: CandleConfig) -> str | None:
    if is_inverted_hammer(bar, cfg):
        return "inverted hammer"
    if is_weak_middle(bar, cfg):
        return "weak middle body"
    if is_strong_bear(bar, cfg):
        return "strong red body"
    return None


def day2_long_pattern(bar: Candle, cfg: CandleConfig) -> str | None:
    if is_hammer(bar, cfg):
        return "hammer"
    if is_weak_middle(bar, cfg):
        return "weak middle body"
    if is_strong_bull(bar, cfg):
        return "strong green body"
    return None


def reversal_setup(
    yesterday: Candle,
    today: Candle,
    yesterday_rsi: float | None,
    *,
    call_threshold: float,
    put_threshold: float,
    cfg: CandleConfig,
) -> tuple[SignalType, str] | None:
    """Short/long only on the reversal day, never on the RSI-stretch strong bar."""
    if yesterday_rsi is None:
        return None
    if is_strong_bull(yesterday, cfg) and yesterday_rsi >= call_threshold:
        pattern = day2_short_pattern(today, cfg)
        if pattern:
            return SignalType.RSI_CANDLE_SHORT, pattern
        return None
    if is_strong_bear(yesterday, cfg) and yesterday_rsi <= put_threshold:
        pattern = day2_long_pattern(today, cfg)
        if pattern:
            return SignalType.RSI_CANDLE_LONG, pattern
        return None
    return None


def same_day_setup(
    today: Candle,
    *,
    rsi_at_close: float | None,
    rsi_at_high: float | None,
    rsi_at_low: float | None,
    call_threshold: float,
    put_threshold: float,
    cfg: CandleConfig,
) -> tuple[SignalType, str] | None:
    """Same session: RSI stretched to 70/30 and by the close the bar is a reversal.

    ``rsi_at_high`` / ``rsi_at_low`` catch a print that tagged 70/30 on the day's
    extreme even if the close has already reversed. A strong bull at RSI 70 is
    still not a short — only the three bearish (or three bullish) shapes.
    """
    tagged_70 = (
        (rsi_at_high is not None and rsi_at_high >= call_threshold)
        or (rsi_at_close is not None and rsi_at_close >= call_threshold)
    )
    if tagged_70:
        pattern = day2_short_pattern(today, cfg)
        if pattern:
            return SignalType.RSI_CANDLE_SHORT, pattern
    tagged_30 = (
        (rsi_at_low is not None and rsi_at_low <= put_threshold)
        or (rsi_at_close is not None and rsi_at_close <= put_threshold)
    )
    if tagged_30:
        pattern = day2_long_pattern(today, cfg)
        if pattern:
            return SignalType.RSI_CANDLE_LONG, pattern
    return None


def candle_stop_price(
    signal: SignalType,
    *,
    reversal: Candle,
    prior: Candle | None = None,
    same_day: bool = False,
) -> float:
    """Stop for both lots: short at the bar high, long at the bar low.

    Two-candle setup: the bar *before* the reversal (the RSI stretch).
    Same-day RSI + reversal: the reversal bar itself.
    """
    bar = reversal if same_day or prior is None else prior
    if signal is SignalType.RSI_CANDLE_SHORT:
        return bar.high
    return bar.low


def waiting_reason(
    yesterday: Candle,
    yesterday_rsi: float | None,
    *,
    call_threshold: float,
    put_threshold: float,
    cfg: CandleConfig,
) -> str | None:
    """Day-1 stretch with no day-2 reversal yet — do not trade."""
    if yesterday_rsi is None:
        return None
    if is_strong_bull(yesterday, cfg) and yesterday_rsi >= call_threshold:
        return (
            f"strong bull at RSI {yesterday_rsi:.1f} — waiting for "
            "inverted hammer / weak middle / strong red"
        )
    if is_strong_bear(yesterday, cfg) and yesterday_rsi <= put_threshold:
        return (
            f"strong bear at RSI {yesterday_rsi:.1f} — waiting for "
            "hammer / weak middle / strong green"
        )
    return None


def make_candle_alert(
    *,
    symbol: str,
    ltp: float,
    rsi: float,
    signal: SignalType,
    pattern: str,
    skip_reason: str | None = None,
    stop_price: float | None = None,
) -> ScanAlert:
    status = f"not taking ({skip_reason})" if skip_reason else f"{pattern} — taking"
    side = "short" if signal is SignalType.RSI_CANDLE_SHORT else "long"
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
        stop_price=stop_price,
        message=f"{symbol}: RSI {rsi:.1f} {side} after {pattern} — {status}",
    )
