#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
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
from src.scan_slots import SESSION_END, now_ist, should_run_slot, write_last_slot
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
        "--slot-guard",
        action="store_true",
        help=(
            "Only run if the current 30-minute IST slot (from 09:30) has not "
            "completed yet. Used by GitHub Actions so late/retry crons catch up "
            "without double-scanning."
        ),
    )
    parser.add_argument(
        "--symbol",
        help="Scan only one symbol (overrides watchlist)",
    )
    args = parser.parse_args()

    config = load_config(args.config)

    if args.symbol:
        config.watchlist = [args.symbol.upper()]

    if args.slot_guard:
        # Manual "Run workflow" always scans; scheduled polls only run unpaid slots.
        force = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
        run, reason, slot = should_run_slot(force=force)
        print(f"Slot guard: {reason}")
        if not run:
            return
    else:
        slot = None

    try:
        scanner = OIRsiScanner(config)
    except MarketDataError as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    try:
        with scanner:
            if args.once:
                if args.slot_guard and slot is not None:
                    remaining = (slot - now_ist()).total_seconds()
                    if remaining > 1:
                        print(
                            f"Warmup: sleeping {int(remaining)}s until "
                            f"{slot:%H:%M} IST so Telegram can leave by "
                            f"{slot.strftime('%H:%M')} + 5 minutes."
                        )
                        time.sleep(remaining)
                scanner.run_once()
                if args.slot_guard and slot is not None:
                    paid = slot
                    clock = now_ist()
                    # Same-day catch-up after 16:00: mark through the close slot
                    # so 15:30/15:45 are not left unpaid overnight.
                    if clock.time() >= SESSION_END:
                        paid = clock.replace(
                            hour=15, minute=45, second=0, microsecond=0
                        )
                    write_last_slot(paid)
                    print(f"Marked scan slot {paid:%Y-%m-%d %H:%M} IST complete.")
            else:
                run_scheduled(scanner)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
