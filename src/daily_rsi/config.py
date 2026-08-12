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
