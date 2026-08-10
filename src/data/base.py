from __future__ import annotations

import time
from pathlib import Path

import requests

CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache"


class MarketDataError(RuntimeError):
    """Raised when live market data cannot be fetched."""


class CredentialsError(MarketDataError):
    """Raised when Angel One credentials are missing or rejected."""


def download_cached(url: str, filename: str, max_age_seconds: int = 86_400) -> Path:
    """Download the instrument master, reusing today's copy when present."""
    CACHE_DIR.mkdir(exist_ok=True)
    target = CACHE_DIR / filename

    if target.exists() and (time.time() - target.stat().st_mtime) < max_age_seconds:
        return target

    response = requests.get(url, timeout=120)
    response.raise_for_status()
    target.write_bytes(response.content)
    return target
