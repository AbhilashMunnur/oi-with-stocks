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
    provider: str
    history_days: int
    option_chain_delay_seconds: float = 3.0


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
class AppConfig:
    rsi: RSIConfig
    oi: OIConfig
    data: DataConfig
    watchlist: list[str]
    schedule: ScheduleConfig
    notifications: NotificationConfig


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    config_path = Path(path)
    with config_path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)

    return AppConfig(
        rsi=RSIConfig(**raw["rsi"]),
        oi=OIConfig(**raw["oi"]),
        data=DataConfig(**raw["data"]),
        watchlist=list(raw["watchlist"]),
        schedule=ScheduleConfig(**raw["schedule"]),
        notifications=NotificationConfig(**raw["notifications"]),
    )
