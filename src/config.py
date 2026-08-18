from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import yaml


class SignalType(str, Enum):
    CALL_OI = "CALL_OI"
    PUT_OI = "PUT_OI"
    ST_BEARISH = "ST_BEARISH"  # Below Supertrend, bearish OI at ST strike → short
    ST_BULLISH = "ST_BULLISH"  # Above Supertrend, bullish OI at ST strike → long


@dataclass
class RSIConfig:
    period: int
    call_threshold: float
    put_threshold: float


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
    # Skip CALL short when put writing / call writing exceeds this (ΔPCR).
    max_change_pcr: float = 1.0
    # PUT longs: require Put ΔOI > 0 at the max Put OI strike
    # (Call ΔOI is also measured at that same strike).
    require_put_writing: bool = True
    # Skip PUT long when put writing / call writing is below this (ΔPCR).
    min_change_pcr: float = 1.0


@dataclass
class DataConfig:
    history_days: int


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
    first_target_pct: float
    second_target_pct: float
    stop_loss_pct: float
    margin_pct: float
    ledger_path: str
    journal_csv: str
    google_sheet_id: str = ""
    google_worksheet: str = ""
    # 1 = current month futures, 3 = far month (e.g. August → October).
    futures_month: int = 3
    # After the first lot is booked at first_target_pct, the remaining lot's
    # stop tightens to this % adverse from the original entry price.
    second_lot_stop_pct: float = 1.0
    # Label on Telegram dashboards so RSI and Supertrend books stay distinct.
    name: str = "Paper"


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
    supertrend: SupertrendConfig = field(default_factory=SupertrendConfig)
    # Separate capital / ledger / journal from the RSI+OI paper book.
    supertrend_paper_trading: PaperTradingConfig | None = None


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    config_path = Path(path)
    with config_path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)

    watchlist = raw["watchlist"]
    st_raw = raw.get("supertrend") or {}
    st_paper_raw = raw.get("supertrend_paper_trading")

    return AppConfig(
        rsi=RSIConfig(**raw["rsi"]),
        oi=OIConfig(**raw["oi"]),
        data=DataConfig(**raw["data"]),
        watchlist=watchlist if isinstance(watchlist, str) else list(watchlist),
        schedule=ScheduleConfig(**raw["schedule"]),
        notifications=NotificationConfig(**raw["notifications"]),
        paper_trading=PaperTradingConfig(**raw["paper_trading"]),
        supertrend=SupertrendConfig(**st_raw),
        supertrend_paper_trading=(
            PaperTradingConfig(**st_paper_raw) if st_paper_raw else None
        ),
    )
