from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class DailyRsiConfig:
    enabled: bool
    rsi_period: int
    rsi_threshold: float
    history_days: int
    watchlist_path: str
    # Yahoo suffix for the market, e.g. "" for US, ".NS" for NSE India.
    yahoo_suffix: str = ""
    market_label: str = "Market"
    currency_symbol: str = "$"
    # Also scan weekly RSI on the same post-close run.
    weekly_enabled: bool = False
    # Defaults to rsi_threshold when omitted (India uses 32 for both).
    weekly_rsi_threshold: float | None = None

    @property
    def weekly_threshold(self) -> float:
        return self.rsi_threshold if self.weekly_rsi_threshold is None else self.weekly_rsi_threshold


def load_daily_rsi_config(path: str | Path, section: str) -> DailyRsiConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return DailyRsiConfig(**raw[section])


def load_watchlist(path: str | Path) -> list[str]:
    text = Path(path).read_text(encoding="utf-8")
    symbols: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        symbol = line.split("#", 1)[0].strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        symbols.append(symbol)
    return symbols
