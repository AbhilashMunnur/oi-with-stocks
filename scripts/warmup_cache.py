#!/usr/bin/env python3
"""Prefetch daily candles for every F&O stock so market scans stay under rate limits."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import load_config
from src.data.angelone_client import AngelOneClient


def main() -> None:
    config = load_config(ROOT / "config.yaml")
    client = AngelOneClient(
        rsi_period=config.rsi.period,
        history_days=config.data.history_days,
    )
    try:
        symbols = client.fno_symbols()
        print(f"Warming daily-close cache for {len(symbols)} symbols...")
        for index, symbol in enumerate(symbols, 1):
            client.daily_closes(symbol)
            if index % 25 == 0:
                print(f"  cached {index}/{len(symbols)}")
                client._save_closes_cache()
        client._save_closes_cache()
        print("Warmup complete.")
    finally:
        client.close()


if __name__ == "__main__":
    main()
