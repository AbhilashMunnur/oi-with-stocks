from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import yaml


class SignalType(str, Enum):
    CALL_OI = "CALL_OI"
    PUT_OI = "PUT_OI"


@dataclass
class RSIConfig:
    period: int
    call_threshold: float
    put_threshold: float


@dataclass
class OIConfig:
    proximity_pct: float


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


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    config_path = Path(path)
    with config_path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)

    watchlist = raw["watchlist"]

    return AppConfig(
        rsi=RSIConfig(**raw["rsi"]),
        oi=OIConfig(**raw["oi"]),
        data=DataConfig(**raw["data"]),
        watchlist=watchlist if isinstance(watchlist, str) else list(watchlist),
        schedule=ScheduleConfig(**raw["schedule"]),
        notifications=NotificationConfig(**raw["notifications"]),
        paper_trading=PaperTradingConfig(**raw["paper_trading"]),
    )
