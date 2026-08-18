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

SUMMARY_COLUMNS = [
    "Date",
    "Time",
    "Total number of positions taken",
    "Capital used in positions",
    "Profit or loss",
    "Total realised profit or loss",
    "Unrealised profit or loss",
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


def build_summary_row(
    *,
    positions: int,
    capital_used: float,
    realised_pnl: float,
    unrealised_pnl: float,
    recorded_at: datetime | None = None,
) -> dict:
    recorded_at = recorded_at or datetime.now()
    return {
        "Date": recorded_at.strftime(DATE_FORMAT),
        "Time": recorded_at.strftime("%H:%M"),
        "Total number of positions taken": positions,
        "Capital used in positions": round(capital_used),
        "Profit or loss": round(realised_pnl + unrealised_pnl),
        "Total realised profit or loss": round(realised_pnl),
        "Unrealised profit or loss": round(unrealised_pnl),
    }


class TradeJournal:
    """Appends closed trades to CSV, and mirrors them to Google Sheets if configured."""

    def __init__(
        self,
        csv_path: str | Path,
        sheet_id: str = "",
        worksheet: str = "",
        summary_worksheet: str = "",
    ):
        self.csv_path = Path(csv_path)
        self.sheet_id = sheet_id
        self.worksheet = worksheet or "Paper trades"
        self.summary_worksheet = summary_worksheet

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
        sheet = self._ensure_worksheet(spreadsheet, self.worksheet, COLUMNS)
        sheet.append_rows([[row[column] for column in COLUMNS] for row in rows])
        print(f"  Logged {len(rows)} trade(s) to Google Sheets → '{self.worksheet}'.")

    @staticmethod
    def _ensure_worksheet(spreadsheet, name: str, columns: list[str]):
        import gspread

        try:
            sheet = spreadsheet.worksheet(name)
        except gspread.WorksheetNotFound:
            sheet = spreadsheet.add_worksheet(name, rows=2000, cols=len(columns))

        existing = sheet.get_all_values()
        if not existing:
            sheet.append_row(columns)
        elif existing[0] != columns and existing[0][:3] != columns[:3]:
            sheet.insert_row(columns, 1)
        return sheet

    def ensure_summary_sheet(self) -> None:
        if not self.sheet_id or not self.summary_worksheet:
            return
        import gspread

        client = gspread.authorize(self._credentials())
        spreadsheet = client.open_by_key(self.sheet_id)
        self._ensure_worksheet(spreadsheet, self.summary_worksheet, SUMMARY_COLUMNS)

    def append_summary(self, row: dict) -> None:
        """Append one RSI+OI portfolio snapshot after a completed scan."""
        if not self.sheet_id or not self.summary_worksheet:
            return
        try:
            import gspread

            client = gspread.authorize(self._credentials())
            spreadsheet = client.open_by_key(self.sheet_id)
            sheet = self._ensure_worksheet(
                spreadsheet, self.summary_worksheet, SUMMARY_COLUMNS
            )
            sheet.append_row([row[column] for column in SUMMARY_COLUMNS])
            print(
                "  Logged RSI+OI portfolio snapshot to Google Sheets → "
                f"'{self.summary_worksheet}'."
            )
        except Exception as exc:
            # Portfolio reporting must never stop scanning or paper trading.
            print(f"  Google Sheet portfolio summary not updated: {exc}")
