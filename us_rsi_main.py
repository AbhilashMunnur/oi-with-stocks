#!/usr/bin/env python3
"""US Nasdaq-100 RSI oversold alerts after cash close (daily + weekly)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import NotificationConfig
from src.daily_rsi import format_rsi_digest, load_watchlist, scan_oversold
from src.notifications.notifier import Notifier
from src.us_rsi import load_us_rsi_config


def _run_timeframe(
    config,
    symbols: list[str],
    *,
    interval: str,
    timeframe_label: str,
    threshold: float,
) -> str:
    print(
        f"Scanning {len(symbols)} Nasdaq-100 names for {timeframe_label} RSI ≤ "
        f"{threshold:g} (period {config.rsi_period})..."
    )
    hits = scan_oversold(
        symbols,
        rsi_period=config.rsi_period,
        rsi_threshold=threshold,
        history_days=config.history_days,
        yahoo_suffix=config.yahoo_suffix,
        interval=interval,
    )
    digest = format_rsi_digest(
        hits,
        threshold=threshold,
        market_label=config.market_label,
        currency_symbol=config.currency_symbol,
        timeframe_label=timeframe_label,
    )
    print(digest)
    return digest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan Nasdaq-100 for daily/weekly RSI at or below the threshold."
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
    digests = [
        _run_timeframe(
            config,
            symbols,
            interval="1d",
            timeframe_label="daily",
            threshold=config.rsi_threshold,
        ),
    ]
    if config.weekly_enabled:
        digests.append(
            _run_timeframe(
                config,
                symbols,
                interval="1wk",
                timeframe_label="weekly",
                threshold=config.weekly_threshold,
            )
        )

    notifier = Notifier(
        NotificationConfig(console=True, telegram=True, cooldown_minutes=0)
    )
    if not notifier.telegram_ready:
        print("Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID).")
        return

    for digest in digests:
        delivered = notifier.send_message(digest)
        print(f"Sent US RSI digest to Telegram ({delivered}/{len(notifier.chat_ids)}).")


if __name__ == "__main__":
    main()
