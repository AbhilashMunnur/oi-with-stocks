#!/usr/bin/env python3
"""Once-daily US Nasdaq RSI oversold alerts (after cash close)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import NotificationConfig
from src.notifications.notifier import Notifier
from src.us_rsi import (
    format_us_rsi_digest,
    load_us_rsi_config,
    load_watchlist,
    scan_oversold,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan top Nasdaq names for RSI at or below the threshold."
    )
    parser.add_argument(
        "--config",
        default="config_us_rsi.yaml",
        help="Path to US RSI config (default: config_us_rsi.yaml)",
    )
    args = parser.parse_args()

    config = load_us_rsi_config(args.config)
    if not config.enabled:
        print("us_rsi.enabled is false — exiting.")
        return

    symbols = load_watchlist(config.watchlist_path)
    print(
        f"Scanning {len(symbols)} Nasdaq names for RSI ≤ {config.rsi_threshold:g} "
        f"(period {config.rsi_period})..."
    )

    hits = scan_oversold(
        symbols,
        rsi_period=config.rsi_period,
        rsi_threshold=config.rsi_threshold,
        history_days=config.history_days,
    )
    digest = format_us_rsi_digest(hits, threshold=config.rsi_threshold)
    print(digest)

    # Reuse the same Telegram bot / chat IDs as the NSE scanner.
    notifier = Notifier(
        NotificationConfig(console=True, telegram=True, cooldown_minutes=0)
    )
    if notifier.telegram_ready:
        delivered = notifier.send_message(digest)
        print(f"Sent US RSI digest to Telegram ({delivered}/{len(notifier.chat_ids)}).")
    else:
        print("Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID).")


if __name__ == "__main__":
    main()
