#!/usr/bin/env python3
"""Ensure Google Sheets tabs exist for each paper book.

Creates the same pair RSI already uses:
  - Paper trades (closed lots)
  - Portfolio Summary (half-hour snapshots)

1. Create a service account in Google Cloud and download its JSON key
2. Share the spreadsheet with the service account email as Editor
3. Put the JSON path (or contents) in GOOGLE_SERVICE_ACCOUNT_JSON
4. Run this script to create/verify the worksheet tabs
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import load_config
from src.paper_trading.journal import TradeJournal


def main() -> None:
    load_dotenv(ROOT / ".env")
    config = load_config(ROOT / "config.yaml")

    books = [("RSI+OI", config.paper_trading)]
    if config.rsi_s1_paper_trading:
        books.append(("RSI+OI S1", config.rsi_s1_paper_trading))
    if config.supertrend_paper_trading:
        books.append(("Supertrend", config.supertrend_paper_trading))

    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        print("GOOGLE_SERVICE_ACCOUNT_JSON is missing from .env.")
        sys.exit(1)

    if raw.endswith(".json"):
        info = json.loads(Path(raw).expanduser().read_text())
    else:
        info = json.loads(raw)

    email = info.get("client_email", "?")
    print(f"Service account: {email}")
    print(f"Make sure the sheet is shared with {email} as Editor.\n")

    for label, paper in books:
        if not paper.google_sheet_id:
            print(f"{label}: google_sheet_id empty — skip")
            continue

        print(f"{label}")
        print(f"  Sheet ID:  {paper.google_sheet_id}")
        print(f"  Trades:    {paper.google_worksheet or '(none)'}")
        print(f"  Summary:   {paper.google_summary_worksheet or '(none)'}")

        journal = TradeJournal(
            csv_path=ROOT / "data" / f"_sheets_probe_{label.replace('+', '_')}.csv",
            sheet_id=paper.google_sheet_id,
            worksheet=paper.google_worksheet,
            summary_worksheet=paper.google_summary_worksheet,
        )
        try:
            journal.ensure_trade_sheet()
            print(f"  OK — '{paper.google_worksheet}' ready (headers only).")
            if paper.google_summary_worksheet:
                journal.ensure_summary_sheet()
                print(f"  OK — '{paper.google_summary_worksheet}' ready (headers only).")
            print()
        except Exception as exc:
            print(f"  FAILED: {exc}\n")
            sys.exit(1)

    print("Tabs are ready. Hosted scans append closed lots and half-hour summaries.")


if __name__ == "__main__":
    main()
