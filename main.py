#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import schedule

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import load_config
from src.data.provider import ProviderError
from src.scanner import OIRsiScanner


def run_scheduled(scanner: OIRsiScanner, config_path: str) -> None:
    def job() -> None:
        if scanner.is_market_hours():
            scanner.run_once()
        else:
            print(f"[{datetime.now():%H:%M:%S}] Outside market hours, skipping scan.")

    interval = load_config(config_path).schedule.interval_minutes
    schedule.every(interval).minutes.do(job)

    print(f"Scheduler running every {interval} minutes. Press Ctrl+C to stop.")
    job()

    while True:
        schedule.run_pending()
        time.sleep(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan NSE F&O stocks for RSI + max OI proximity alerts."
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config file (default: config.yaml)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single scan and exit",
    )
    parser.add_argument(
        "--symbol",
        help="Scan only one symbol (overrides watchlist)",
    )
    parser.add_argument(
        "--provider",
        choices=["dhan", "angelone"],
        help="Override the live data provider set in config",
    )
    args = parser.parse_args()

    config = load_config(args.config)

    if args.provider:
        config.data.provider = args.provider
    if args.symbol:
        config.watchlist = [args.symbol.upper()]

    try:
        scanner = OIRsiScanner(config)
    except ProviderError as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    if args.once:
        with scanner:
            scanner.run_once()
        return

    with scanner:
        run_scheduled(scanner, args.config)


if __name__ == "__main__":
    main()
