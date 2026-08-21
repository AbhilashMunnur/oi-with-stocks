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

    last_error: Exception | None = None
    for attempt in range(1, 4):
        tmp = target.with_suffix(target.suffix + ".tmp")
        try:
            with requests.get(url, timeout=120, stream=True) as response:
                response.raise_for_status()
                with tmp.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=256 * 1024):
                        if chunk:
                            handle.write(chunk)
            tmp.replace(target)
            return target
        except (requests.RequestException, OSError) as exc:
            last_error = exc
            print(f"  download {filename} attempt {attempt}/3 failed: {exc}")
            time.sleep(2 * attempt)
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)

        if last_error and target.exists():
            print(f"  download {filename} failed; reusing the cached copy")
            return target

    raise last_error or RuntimeError(f"download failed: {url}")
