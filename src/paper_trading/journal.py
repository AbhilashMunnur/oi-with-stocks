from __future__ import annotations

import csv
import json
import os
from datetime import datetime
from pathlib import Path

import numpy as np

# Same columns as the Nifty backtesting sheet, plus Symbol (multi-stock) and
# Exit reason (scale-out / stop / expiry).
COLUMNS = [
    "Symbol",
    "Buy/Sell",
    "Entry date",
    "Entry price",
    "Entry RSI",
    "Exit date",
    "Exit price",
    "Exit RSI",
    "Holding trading period",
    "Capital needed",
    "Profit/loss",
    "Exit reason",
]

DATE_FORMAT = "%d-%b-%y"


def trading_days_between(entry: datetime, exit_: datetime) -> int:
    """Weekdays between two dates, matching the journal's holding period."""
    return int(np.busday_count(entry.date(), exit_.date()))


def capital_needed(margin_per_lot: float, lots: int) -> str:
    """Match the sheet style, e.g. 180000*2."""
    return f"{int(round(margin_per_lot))}*{lots}"


def build_row(
    *,
    symbol: str,
    direction: str,
    entry_time: str,
    entry_price: float,
    entry_rsi: float,
    exit_time: str,
    exit_price: float,
    exit_rsi: float | None,
    lots: int,
    margin_per_lot: float,
    pnl: float,
    reason: str,
) -> dict:
    entry_dt = datetime.strptime(entry_time, "%Y-%m-%d %H:%M:%S")
    exit_dt = datetime.strptime(exit_time, "%Y-%m-%d %H:%M:%S")

    return {
        "Symbol": symbol,
        "Buy/Sell": "Buy" if direction == "LONG" else "Sell",
        "Entry date": entry_dt.strftime(DATE_FORMAT),
        "Entry price": round(entry_price, 2),
        "Entry RSI": round(entry_rsi, 1),
        "Exit date": exit_dt.strftime(DATE_FORMAT),
        "Exit price": round(exit_price, 2),
        "Exit RSI": round(exit_rsi, 1) if exit_rsi is not None else "",
        "Holding trading period": trading_days_between(entry_dt, exit_dt),
        "Capital needed": capital_needed(margin_per_lot, lots),
        "Profit/loss": round(pnl),
        "Exit reason": reason,
    }


class TradeJournal:
    """Appends closed trades to CSV, and mirrors them to Google Sheets if configured."""

    def __init__(self, csv_path: str | Path, sheet_id: str = "", worksheet: str = ""):
        self.csv_path = Path(csv_path)
        self.sheet_id = sheet_id
        self.worksheet = worksheet or "Paper trades"

    def append(self, rows: list[dict]) -> None:
        if not rows:
            return

        self._append_csv(rows)

        if self.sheet_id:
            try:
                self._append_sheet(rows)
            except Exception as exc:
                # A logging failure must never cost us the trade record.
                print(f"  Google Sheet not updated: {exc}")

    def _append_csv(self, rows: list[dict]) -> None:
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not self.csv_path.exists()

        with self.csv_path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=COLUMNS)
            if is_new:
                writer.writeheader()
            writer.writerows(rows)

    def _credentials(self):
        from google.oauth2.service_account import Credentials

        raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
        if not raw:
            raise RuntimeError(
                "GOOGLE_SERVICE_ACCOUNT_JSON is not set. "
                "Put the service-account JSON in .env (or a path to the file), "
                "and share the sheet with that service account as Editor."
            )

        info = json.loads(Path(raw).read_text()) if raw.endswith(".json") else json.loads(raw)
        return Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )

    def _append_sheet(self, rows: list[dict]) -> None:
        import gspread

        client = gspread.authorize(self._credentials())
        spreadsheet = client.open_by_key(self.sheet_id)

        try:
            sheet = spreadsheet.worksheet(self.worksheet)
        except gspread.WorksheetNotFound:
            sheet = spreadsheet.add_worksheet(self.worksheet, rows=2000, cols=len(COLUMNS))

        existing = sheet.get_all_values()
        if not existing:
            sheet.append_row(COLUMNS)
        elif existing[0] != COLUMNS:
            # Don't overwrite the historical Nifty sheet; ensure our tab has headers.
            if existing[0][:3] != COLUMNS[:3]:
                sheet.insert_row(COLUMNS, 1)

        sheet.append_rows([[row[column] for column in COLUMNS] for row in rows])
        print(f"  Logged {len(rows)} trade(s) to Google Sheets → '{self.worksheet}'.")
