#!/usr/bin/env python3
"""Ensure Google Sheets tabs exist for paper journals and RSI portfolio snapshots.

1. Create a service account in Google Cloud and download its JSON key
2. Share the spreadsheet with the service account email as Editor
3. Put the JSON path (or contents) in GOOGLE_SERVICE_ACCOUNT_JSON
4. Run this script to create/verify both worksheet tabs
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
from src.paper_trading.journal import COLUMNS, TradeJournal


def _probe_row(label: str) -> dict:
    probe = {column: "" for column in COLUMNS}
    probe.update(
        {
            "Symbol": "PROBE",
            "Buy/Sell": "Buy",
            "Entry date": "12-Aug-26",
            "Entry price": 0,
            "Entry RSI": 0,
            "Exit date": "12-Aug-26",
            "Exit price": 0,
            "Exit RSI": 0,
            "Holding trading period": 0,
            "Capital needed": "0*0",
            "Profit/loss": 0,
            "Exit reason": f"setup_probe_{label}",
        }
    )
    return probe


def main() -> None:
    load_dotenv(ROOT / ".env")
    config = load_config(ROOT / "config.yaml")

    books = [("RSI+OI", config.paper_trading)]
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
        print(f"  Worksheet: {paper.google_worksheet}")

        journal = TradeJournal(
            csv_path=ROOT / "data" / f"_sheets_probe_{label.replace('+', '_')}.csv",
            sheet_id=paper.google_sheet_id,
            worksheet=paper.google_worksheet,
            summary_worksheet=paper.google_summary_worksheet,
        )
        try:
            journal._append_sheet([_probe_row(label)])
            print("  OK — tab ready (PROBE row written; delete it if you like).\n")
            if paper.google_summary_worksheet:
                journal.ensure_summary_sheet()
                print(
                    "  Portfolio summary: "
                    f"{paper.google_summary_worksheet} — ready.\n"
                )
        except Exception as exc:
            print(f"  FAILED: {exc}\n")
            sys.exit(1)

    print("Both journals are ready. Hosted scans will append closed lots after booking.")


if __name__ == "__main__":
    main()
