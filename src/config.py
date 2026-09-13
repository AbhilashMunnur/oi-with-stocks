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
    # Heikin-Ashi strong → weak → opposite-colour sequence + normal reversal candle.
    HA_SHORT = "HA_SHORT"
    HA_LONG = "HA_LONG"


SHORT_SIGNALS = frozenset({"RSI_CANDLE_SHORT", "HA_SHORT"})
CANDLE_SIGNALS = frozenset({"RSI_CANDLE_SHORT", "RSI_CANDLE_LONG"})
HA_SIGNALS = frozenset({"HA_SHORT", "HA_LONG"})


@dataclass
class RSIConfig:
    period: int
    call_threshold: float
    put_threshold: float


@dataclass
class CandleConfig:
    """Day-2 reversal shapes after an RSI 70/30 strong bar."""

    strong_body_pct: float = 50.0
    weak_body_pct: float = 40.0
    side_wick_pct: float = 20.0
    hammer_long_wick_pct: float = 50.0
    hammer_short_wick_pct: float = 15.0


@dataclass
class HeikinAshiConfig:
    """Heikin_Ashi book: RSI 70/30 tag, then strong → weak → opposite HA candle.

    Shorts: RSI ≥ 70 within ``rsi_lookback_sessions``; a strong green HA
    candle (body ≥ strong_body_pct of its range), then at least
    ``min_weak_candles`` weak HA candles (body ≤ weak_body_pct, any colour),
    then today's HA candle turns red with a real body, and today's normal
    candle is one of the usual bearish reversal shapes. Longs mirror.
    """

    strong_body_pct: float = 50.0
    weak_body_pct: float = 40.0
    min_weak_candles: int = 1
    rsi_lookback_sessions: int = 10


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
    # RSI_CandlePattern: race percent targets vs SMMA — whichever prints first.
    # Lot 1: first_target_pct (e.g. 5%) or SMMA fast (21). Lot 2: second_target_pct
    # (e.g. 12%) or SMMA slow (50). RSI 30/70 can still take lot 2 earlier.
    smma_fast: int | None = None
    smma_slow: int | None = None
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
    # Store a bar stop at entry. When on, the stored level is the farther of
    # the reversal-bar high/low and stop_loss_pct from the fill. Turn this off
    # to use only the percent stop.
    candle_stop: bool = True
    # Three-lot books: once every other lot is booked, the last lot rides for
    # RSI 30/70. These two govern how that runner behaves.
    #   final_lot_smma_cross_exit — also close it when a strong-bodied candle
    #     closes back through SMMA fast, i.e. the move it was riding is over.
    #   final_lot_no_stop — carry no stop on the runner at all.
    final_lot_smma_cross_exit: bool = False
    final_lot_no_stop: bool = False
    # Separate 3-lot book: book every remaining lot only when price reverses
    # at this SMMA *and* the SMMA slope has turned against the trade
    # (short: 9 rising; long: 9 falling) without closing through the line
    # by 15:15. A close through the line, or a bounce while the 9 still
    # slopes with the trade, means hold.
    smma_reversal: int | None = None
    smma_reversal_exit: bool = False
    # Per-book RSI gate on candle entries. The scanner screens every name at
    # the global rsi.call_threshold / put_threshold; a book with its own
    # levels only takes shorts whose entry RSI is at or above rsi_call_threshold
    # and longs at or below rsi_put_threshold. None = use the global level.
    rsi_call_threshold: float | None = None
    rsi_put_threshold: float | None = None


@dataclass
class AppConfig:
    rsi: RSIConfig
    oi: OIConfig
    data: DataConfig
    # Either an explicit list of symbols or the string "all" for every F&O stock.
    watchlist: list[str] | str
    schedule: ScheduleConfig
    notifications: NotificationConfig
    paper_trading: PaperTradingConfig | None = None
    candles: CandleConfig = field(default_factory=CandleConfig)
    # Live book: candle / wider-of 2% stop. Do not change stops on open names.
    rsi_candle_2w_paper_trading: PaperTradingConfig | None = None
    # Final strategy: 3 lots, Rs 4 Cr, flat 2% stop. Same RSI+candle entries.
    rsi_candle_3lot_paper_trading: PaperTradingConfig | None = None
    # Heikin_Ashi book: same ladder as the 3-lot book, HA-sequence entries.
    heikin_ashi: HeikinAshiConfig = field(default_factory=HeikinAshiConfig)
    heikin_ashi_paper_trading: PaperTradingConfig | None = None
    # Laboratory names: never short on any scanner; longs still allowed.
    no_short_symbols: list[str] = field(default_factory=list)

    def candle_books(self) -> list[PaperTradingConfig]:
        return [
            book
            for book in (
                self.rsi_candle_2w_paper_trading,
                self.rsi_candle_3lot_paper_trading,
                self.heikin_ashi_paper_trading,
            )
            if book is not None
        ]


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
    two_week_raw = raw.get("rsi_candle_2w_paper_trading")
    three_lot_raw = raw.get("rsi_candle_3lot_paper_trading")
    ha_raw = raw.get("heikin_ashi_paper_trading")

    return AppConfig(
        rsi=RSIConfig(**raw["rsi"]),
        oi=OIConfig(**oi_raw),
        data=DataConfig(**raw["data"]),
        watchlist=watchlist if isinstance(watchlist, str) else list(watchlist),
        no_short_symbols=no_short,
        schedule=ScheduleConfig(**raw["schedule"]),
        notifications=NotificationConfig(**raw["notifications"]),
        paper_trading=(
            PaperTradingConfig(**raw["paper_trading"])
            if raw.get("paper_trading")
            else None
        ),
        candles=CandleConfig(**(raw.get("candles") or {})),
        rsi_candle_2w_paper_trading=(
            PaperTradingConfig(**two_week_raw) if two_week_raw else None
        ),
        rsi_candle_3lot_paper_trading=(
            PaperTradingConfig(**three_lot_raw) if three_lot_raw else None
        ),
        heikin_ashi=HeikinAshiConfig(**(raw.get("heikin_ashi") or {})),
        heikin_ashi_paper_trading=(
            PaperTradingConfig(**ha_raw) if ha_raw else None
        ),
    )
