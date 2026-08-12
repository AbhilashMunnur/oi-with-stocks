from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class UsRsiConfig:
    enabled: bool
    rsi_period: int
    rsi_threshold: float
    history_days: int
    watchlist_path: str


def load_us_rsi_config(path: str | Path = "config_us_rsi.yaml") -> UsRsiConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return UsRsiConfig(**raw["us_rsi"])


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
