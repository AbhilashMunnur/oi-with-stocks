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
from src.data.base import MarketDataError
from src.scanner import OIRsiScanner


def run_scheduled(scanner: OIRsiScanner) -> None:
    def job() -> None:
        if not scanner.is_market_hours():
            print(f"[{datetime.now():%a %H:%M:%S}] Market closed, skipping scan.")
            return

        try:
            scanner.run_once()
        except Exception as exc:
            # One bad scan should not end the session; try again next interval.
            print(f"[{datetime.now():%H:%M:%S}] Scan failed: {exc}")

    interval = scanner.config.schedule.interval_minutes
    schedule.every(interval).minutes.do(job)

    window = scanner.config.schedule
    print(
        f"Scanning every {interval} minutes between {window.market_start} and "
        f"{window.market_end} on weekdays. Press Ctrl+C to stop."
    )
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
    args = parser.parse_args()

    config = load_config(args.config)

    if args.symbol:
        config.watchlist = [args.symbol.upper()]

    try:
        scanner = OIRsiScanner(config)
    except MarketDataError as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    try:
        with scanner:
            if args.once:
                scanner.run_once()
            else:
                run_scheduled(scanner)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
