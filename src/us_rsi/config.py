# Kept for import compatibility; implementation lives in src.daily_rsi.
from src.daily_rsi.config import DailyRsiConfig as UsRsiConfig
from src.daily_rsi.config import load_daily_rsi_config, load_watchlist


def load_us_rsi_config(path: str = "config_us_rsi.yaml") -> UsRsiConfig:
    return load_daily_rsi_config(path, "us_rsi")


__all__ = ["UsRsiConfig", "load_us_rsi_config", "load_watchlist"]
