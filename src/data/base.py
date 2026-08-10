from __future__ import annotations

import time
from abc import ABC, abstractmethod
from pathlib import Path

import requests

from src.data.models import OISnapshot, PriceSnapshot

CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache"


class ProviderError(RuntimeError):
    """Raised when a market data provider cannot serve a request."""


class ProviderCredentialsError(ProviderError):
    """Raised when broker API credentials are missing or rejected."""


class MarketDataProvider(ABC):
    """A live market data source supplying both price/RSI and option-chain OI."""

    name: str

    @abstractmethod
    def get_price_snapshot(self, symbol: str) -> PriceSnapshot | None:
        """Live LTP plus RSI computed from recent candles."""

    @abstractmethod
    def get_oi_snapshot(self, symbol: str) -> OISnapshot | None:
        """Nearest-expiry strikes carrying the highest call and put OI."""

    def close(self) -> None:
        return None

    def __enter__(self) -> MarketDataProvider:
        return self

    def __exit__(self, *args) -> None:
        self.close()


def download_cached(url: str, filename: str, max_age_seconds: int = 86_400) -> Path:
    """Download an instrument master file, reusing today's copy when present."""
    CACHE_DIR.mkdir(exist_ok=True)
    target = CACHE_DIR / filename

    if target.exists() and (time.time() - target.stat().st_mtime) < max_age_seconds:
        return target

    response = requests.get(url, timeout=120)
    response.raise_for_status()
    target.write_bytes(response.content)
    return target
