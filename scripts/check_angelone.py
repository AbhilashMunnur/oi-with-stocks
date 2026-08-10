#!/usr/bin/env python3
"""Preflight check for Angel One credentials and live data access.

Run this once after filling in .env to confirm every piece the scanner needs
works, before trusting it to raise alerts.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import load_config
from src.data.base import CredentialsError
from src.oi_analyzer import format_oi, format_oi_change

REQUIRED_VARS = ["ANGEL_API_KEY", "ANGEL_CLIENT_CODE", "ANGEL_PIN", "ANGEL_TOTP_SECRET"]


def check_env() -> bool:
    load_dotenv(ROOT / ".env")
    missing = [name for name in REQUIRED_VARS if not os.getenv(name, "").strip()]

    if missing:
        print("Missing from .env: " + ", ".join(missing))
        print("\nCopy .env.example to .env and fill these in from smartapi.angelone.in.")
        print("ANGEL_TOTP_SECRET is the text string shown next to the 2FA QR code,")
        print("not the 6-digit code that rotates.")
        return False

    print("Credentials found in .env")
    return True


def main() -> None:
    symbol = (sys.argv[1] if len(sys.argv) > 1 else "RELIANCE").upper()

    if not check_env():
        sys.exit(1)

    from src.data.angelone_client import AngelOneClient

    config = load_config(ROOT / "config.yaml")

    try:
        client = AngelOneClient(
            rsi_period=config.rsi.period,
            history_days=config.data.history_days,
        )
    except CredentialsError as exc:
        print(f"\nLogin failed: {exc}")
        print("\nCommon causes: wrong PIN, a stale TOTP secret, or the API key not")
        print("yet activated on smartapi.angelone.in.")
        sys.exit(1)

    print(f"Logged in to Angel One ({len(client.fno_symbols())} F&O stocks available)")

    print(f"\nChecking live data for {symbol}...")
    ltp = client.get_ltps([symbol]).get(symbol)
    if not ltp:
        print(f"Could not fetch a live price for {symbol}.")
        client.close()
        sys.exit(1)

    rsi = client.get_rsi(symbol, ltp)
    oi = client.get_oi_snapshot(symbol, ltp=ltp)
    if not oi:
        print(f"Could not fetch the option chain for {symbol}.")
        client.close()
        sys.exit(1)

    client.add_oi_changes(oi)

    rsi_text = f"{rsi:.1f}" if rsi is not None else "unavailable"
    print("\nAll checks passed:")
    print(f"  Symbol          {oi.symbol}")
    print(f"  Live LTP        Rs {ltp:,.2f}")
    print(f"  RSI({config.rsi.period})         {rsi_text}")
    print(f"  Expiry          {oi.expiry}  (lot size {oi.lot_size})")
    print(
        f"  Max Call OI     Rs {oi.max_call_oi_strike:,.0f}  "
        f"({format_oi(oi, oi.max_call_oi)}, ΔOI {format_oi_change(oi, oi.call_oi_change)})"
    )
    print(
        f"  Max Put OI      Rs {oi.max_put_oi_strike:,.0f}  "
        f"({format_oi(oi, oi.max_put_oi)}, ΔOI {format_oi_change(oi, oi.put_oi_change)})"
    )
    pcr = oi.change_pcr
    print(f"  Change PCR      {f'{pcr:.2f}' if pcr is not None else 'n/a (a leg is unwinding)'}")
    print("\nReady to scan: python main.py --once")

    client.close()


if __name__ == "__main__":
    main()
