#!/usr/bin/env python3
"""Check Google Sheets access for the paper-trading journal.

1. Create a service account in Google Cloud and download its JSON key
2. Share the spreadsheet with the service account email as Editor
3. Put the JSON path (or contents) in GOOGLE_SERVICE_ACCOUNT_JSON
4. Run this script to verify a write into the Paper trades tab
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


def main() -> None:
    load_dotenv(ROOT / ".env")
    config = load_config(ROOT / "config.yaml")
    paper = config.paper_trading

    if not paper.google_sheet_id:
        print("paper_trading.google_sheet_id is empty in config.yaml")
        sys.exit(1)

    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        print(
            "GOOGLE_SERVICE_ACCOUNT_JSON is missing from .env.\n\n"
            "Steps:\n"
            "1. Google Cloud Console → create a project → enable Google Sheets API\n"
            "2. Create a service account → download its JSON key\n"
            "3. Share your spreadsheet with the service account email (Editor)\n"
            "4. In .env set:\n"
            "   GOOGLE_SERVICE_ACCOUNT_JSON=/absolute/path/to/key.json\n"
            "   or paste the JSON object on one line"
        )
        sys.exit(1)

    if raw.endswith(".json"):
        info = json.loads(Path(raw).expanduser().read_text())
    else:
        info = json.loads(raw)

    email = info.get("client_email", "?")
    print(f"Service account: {email}")
    print(f"Sheet ID:        {paper.google_sheet_id}")
    print(f"Worksheet:       {paper.google_worksheet}")
    print()
    print(f"Make sure the sheet is shared with {email} as Editor.")
    print()

    journal = TradeJournal(
        csv_path=ROOT / "data" / "_sheets_probe.csv",
        sheet_id=paper.google_sheet_id,
        worksheet=paper.google_worksheet,
    )

    probe = {column: "" for column in COLUMNS}
    probe.update(
        {
            "Symbol": "PROBE",
            "Buy/Sell": "Buy",
            "Entry date": "10-Aug-26",
            "Entry price": 0,
            "Entry RSI": 0,
            "Exit date": "10-Aug-26",
            "Exit price": 0,
            "Exit RSI": 0,
            "Holding trading period": 0,
            "Capital needed": "0*0",
            "Profit/loss": 0,
            "Exit reason": "setup_probe",
        }
    )

    try:
        journal._append_sheet([probe])
    except Exception as exc:
        print(f"Write failed: {exc}")
        sys.exit(1)

    print("Write succeeded. Open the sheet and delete the PROBE row if you like.")
    print("Then sync the secret for hosted scans:")
    print("  ./scripts/sync_github_secrets.sh")


if __name__ == "__main__":
    main()
