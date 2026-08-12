#!/usr/bin/env python3
"""Once-daily India Nifty-100 RSI oversold alerts (after NSE cash close)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import NotificationConfig
from src.daily_rsi import (
    format_rsi_digest,
    load_daily_rsi_config,
    load_watchlist,
    scan_oversold,
)
from src.notifications.notifier import Notifier


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan Nifty-100 names for RSI at or below the threshold."
    )
    parser.add_argument(
        "--config",
        default="config_in_rsi.yaml",
        help="Path to India RSI config (default: config_in_rsi.yaml)",
    )
    args = parser.parse_args()

    config = load_daily_rsi_config(args.config, "in_rsi")
    if not config.enabled:
        print("in_rsi.enabled is false — exiting.")
        return

    symbols = load_watchlist(config.watchlist_path)
    print(
        f"Scanning {len(symbols)} Nifty-100 names for RSI ≤ {config.rsi_threshold:g} "
        f"(period {config.rsi_period})..."
    )

    hits = scan_oversold(
        symbols,
        rsi_period=config.rsi_period,
        rsi_threshold=config.rsi_threshold,
        history_days=config.history_days,
        yahoo_suffix=config.yahoo_suffix,
    )
    digest = format_rsi_digest(
        hits,
        threshold=config.rsi_threshold,
        market_label=config.market_label,
        currency_symbol=config.currency_symbol,
    )
    print(digest)

    notifier = Notifier(
        NotificationConfig(console=True, telegram=True, cooldown_minutes=0)
    )
    if notifier.telegram_ready:
        delivered = notifier.send_message(digest)
        print(f"Sent India RSI digest to Telegram ({delivered}/{len(notifier.chat_ids)}).")
    else:
        print("Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID).")


if __name__ == "__main__":
    main()
