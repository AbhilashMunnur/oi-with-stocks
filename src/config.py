from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import yaml


class SignalType(str, Enum):
    CALL_OI = "CALL_OI"
    PUT_OI = "PUT_OI"
    CALL_OI_S1 = "CALL_OI_S1"
    PUT_OI_S1 = "PUT_OI_S1"
    CALL_OI_S2 = "CALL_OI_S2"
    PUT_OI_S2 = "PUT_OI_S2"
    ST_BEARISH = "ST_BEARISH"  # Below Supertrend, bearish OI at ST strike → short
    ST_BULLISH = "ST_BULLISH"  # Above Supertrend, bullish OI at ST strike → long
    RSI_CANDLE_SHORT = "RSI_CANDLE_SHORT"
    RSI_CANDLE_LONG = "RSI_CANDLE_LONG"


@dataclass
class RSIConfig:
    period: int
    call_threshold: float
    put_threshold: float


@dataclass
class CandleConfig:
    """Day-2 reversal shapes after an RSI 70/30 strong bar."""

    strong_body_pct: float = 60.0
    weak_body_pct: float = 40.0
    side_wick_pct: float = 20.0
    hammer_long_wick_pct: float = 50.0
    hammer_short_wick_pct: float = 15.0


@dataclass
class SupertrendConfig:
    enabled: bool = True
    atr_period: int = 20
    multiplier: float = 4.5
    # Price must be within this % of the Supertrend line (0–0.5% by default).
    proximity_pct: float = 0.5


@dataclass
class OIConfig:
    proximity_pct: float
    # CALL shorts: require Call ΔOI > 0 at the max Call OI strike
    # (Put ΔOI is also measured at that same strike).
    require_call_writing: bool = True
    # CALL shorts require put writing / call writing below this (ΔPCR).
    max_change_pcr: float = 0.75
    # PUT longs: require Put ΔOI > 0 at the max Put OI strike
    # (Call ΔOI is also measured at that same strike).
    require_put_writing: bool = True
    # PUT longs require put writing / call writing above this (ΔPCR).
    min_change_pcr: float = 1.0
    # S1 fallback (uncrossed wall after the peak is through price) must have
    # at least this % of the peak wall's OI, or it is treated as too thin.
    s1_min_fallback_oi_pct: float = 50.0
    # S2 entry: cash must be this close to the uncrossed wall (same 1% as S1).
    s2_proximity_pct: float = 1.0
    # S2 ΔPCR uses this many listed strikes below the wall, the wall, and the
    # same count above. Writing is still required at the wall itself.
    s2_pcr_strikes: int = 1
    # No new paper on last-Tuesday stock monthly expiry (front-month unwind).
    # Applies to RSI+OI, S1, S2, and Supertrend.
    skip_monthly_expiry: bool = True
    # Skip new entries within this % of 52-week / all-time high or low.
    extreme_proximity_pct: float = 2.0
    # After price crosses those highs/lows, no new entries for this many sessions.
    extreme_cooldown_days: int = 2


@dataclass
class DataConfig:
    history_days: int
    # Daily bars for 52-week / all-time high and low (candidates only).
    extreme_history_days: int = 4000


@dataclass
class ScheduleConfig:
    interval_minutes: int
    market_start: str
    market_end: str


@dataclass
class NotificationConfig:
    console: bool
    telegram: bool
    cooldown_minutes: int


@dataclass
class PaperTradingConfig:
    enabled: bool
    capital: float
    lots_per_trade: int
    margin_pct: float
    ledger_path: str
    journal_csv: str
    first_target_pct: float = 6.0
    second_target_pct: float = 11.0
    stop_loss_pct: float = 4.0
    google_sheet_id: str = ""
    google_worksheet: str = ""
    google_summary_worksheet: str = ""
    # 1 = current month futures, 3 = far month (e.g. August → October).
    futures_month: int = 3
    # After the first lot is booked, remaining lots' stop tightens to this
    # % adverse from the original entry price.
    second_lot_stop_pct: float = 1.0
    # RSI_CandlePattern: 1 lot at SMMA fast, remaining lot at SMMA slow. When set,
    # percent targets are ignored on this book.
    smma_fast: int | None = None
    smma_slow: int | None = None
    # After SMMA 21: remaining lot at SMMA 50, or earlier at RSI 30 (shorts)
    # / RSI 70 (longs) if that prints first.
    second_lot_rsi_short: float | None = None
    second_lot_rsi_long: float | None = None
    # Label on Telegram dashboards so RSI and Supertrend books stay distinct.
    name: str = "Paper"
    # Optional third scale-out (S1: 1 lot at 6%, 1 at 10%, rest at 14%).
    third_target_pct: float | None = None
    # After a stop / OI invalidation, do not reopen the same name today.
    block_same_day_reentry: bool = False
    # RSI_CandlePattern: after this many consecutive losing trades whose
    # first-to-last exit sits inside this many book sessions, skip the next
    # N times the name qualifies. 0 disables.
    loss_streak_count: int = 0
    loss_streak_sessions: int = 0
    skip_qualifies_after_streak: int = 0
    # Mark-only books: never open new paper from this scan's alerts.
    skip_new_entries: bool = False
    # Quote the futures expiry stored on each position (do not roll the month).
    mark_entry_contract: bool = False
    # Candle stop fires only when cash closes through the stored bar, not on
    # an intraday futures wick. Intraday slots still mark P&L and SMMA.
    cash_close_stop: bool = False


@dataclass
class AppConfig:
    rsi: RSIConfig
    oi: OIConfig
    data: DataConfig
    # Either an explicit list of symbols or the string "all" for every F&O stock.
    watchlist: list[str] | str
    schedule: ScheduleConfig
    notifications: NotificationConfig
    paper_trading: PaperTradingConfig
    candles: CandleConfig = field(default_factory=CandleConfig)
    supertrend: SupertrendConfig = field(default_factory=SupertrendConfig)
    # Separate capital / ledger / journal from the RSI+OI paper book.
    supertrend_paper_trading: PaperTradingConfig | None = None
    # RSI+OI with Scenario 1 wall filter (broken = unwind + opposite add).
    rsi_s1_paper_trading: PaperTradingConfig | None = None
    # Same S1 entry; OI exit after two consecutive invalid scans.
    rsi_s2_paper_trading: PaperTradingConfig | None = None
    # 19 Aug–2 Sep 3rd-month names still open — mark and Telegram only.
    rsi_candle_2w_paper_trading: PaperTradingConfig | None = None
    # Laboratory names: never short on any scanner; longs still allowed.
    no_short_symbols: list[str] = field(default_factory=list)


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    config_path = Path(path)
    with config_path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)

    watchlist = raw["watchlist"]
    no_short = [str(s).upper() for s in (raw.get("no_short_symbols") or [])]
    oi_raw = dict(raw["oi"])
    if "skip_monthly_expiry" not in oi_raw and "s2_skip_monthly_expiry" in oi_raw:
        oi_raw["skip_monthly_expiry"] = oi_raw.pop("s2_skip_monthly_expiry")
    else:
        oi_raw.pop("s2_skip_monthly_expiry", None)
    st_raw = raw.get("supertrend") or {}
    st_paper_raw = raw.get("supertrend_paper_trading")
    s1_paper_raw = raw.get("rsi_s1_paper_trading")
    s2_paper_raw = raw.get("rsi_s2_paper_trading")
    two_week_raw = raw.get("rsi_candle_2w_paper_trading")

    return AppConfig(
        rsi=RSIConfig(**raw["rsi"]),
        oi=OIConfig(**oi_raw),
        data=DataConfig(**raw["data"]),
        watchlist=watchlist if isinstance(watchlist, str) else list(watchlist),
        no_short_symbols=no_short,
        schedule=ScheduleConfig(**raw["schedule"]),
        notifications=NotificationConfig(**raw["notifications"]),
        paper_trading=PaperTradingConfig(**raw["paper_trading"]),
        candles=CandleConfig(**(raw.get("candles") or {})),
        supertrend=SupertrendConfig(**st_raw),
        supertrend_paper_trading=(
            PaperTradingConfig(**st_paper_raw) if st_paper_raw else None
        ),
        rsi_s1_paper_trading=(
            PaperTradingConfig(**s1_paper_raw) if s1_paper_raw else None
        ),
        rsi_s2_paper_trading=(
            PaperTradingConfig(**s2_paper_raw) if s2_paper_raw else None
        ),
        rsi_candle_2w_paper_trading=(
            PaperTradingConfig(**two_week_raw) if two_week_raw else None
        ),
    )
