#!/usr/bin/env python3
"""Export the live RSI_CandlePattern Google Sheet to a local .xlsx workbook.

Pulls every tab straight from the sheet so the file matches what the scanner
writes, then formats it for reading: frozen headers, filters, column widths,
and red/green P&L.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

import gspread
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from src.config import load_config
from src.paper_trading.journal import TradeJournal

TABS = [
    "RSI Paper trades",
    "RSI Portfolio Summary",
    "RSI P&L from Aug 1",
    "RSI Charges estimate",
]
HEADER_FILL = PatternFill("solid", fgColor="1F3864")
HEADER_FONT = Font(color="FFFFFF", bold=True)
GREEN = Font(color="107C41")
RED = Font(color="C00000")


def looks_numeric(text: str) -> bool:
    stripped = str(text).replace(",", "").replace("-", "", 1).replace(".", "", 1)
    return stripped.isdigit() and str(text).strip() != ""


def main() -> None:
    config = load_config(ROOT / "config.yaml")
    paper = config.rsi_candle_2w_paper_trading
    gc = gspread.authorize(TradeJournal("x", sheet_id=paper.google_sheet_id)._credentials())
    spreadsheet = gc.open_by_key(paper.google_sheet_id)

    workbook = Workbook()
    workbook.remove(workbook.active)

    for title in TABS:
        try:
            values = spreadsheet.worksheet(title).get_all_values()
        except gspread.WorksheetNotFound:
            print(f"  {title}: not found, skipped")
            continue
        if not values:
            continue

        sheet = workbook.create_sheet(title[:31])
        pnl_columns = {
            index
            for index, name in enumerate(values[0])
            if name.strip() in {"Profit/loss", "Day P&L", "Profit or loss"}
        }
        for r, row in enumerate(values, start=1):
            for c, cell in enumerate(row, start=1):
                value = cell
                if looks_numeric(cell):
                    value = float(cell) if "." in cell else int(cell)
                written = sheet.cell(row=r, column=c, value=value)
                if r == 1:
                    written.fill = HEADER_FILL
                    written.font = HEADER_FONT
                    written.alignment = Alignment(vertical="center", wrap_text=True)
                elif (c - 1) in pnl_columns and isinstance(value, (int, float)):
                    written.font = GREEN if value >= 0 else RED
                    written.number_format = "#,##0"

        widths: dict[int, int] = {}
        for row in values:
            for c, cell in enumerate(row, start=1):
                widths[c] = min(max(widths.get(c, 10), len(str(cell)) + 2), 60)
        for c, width in widths.items():
            sheet.column_dimensions[get_column_letter(c)].width = width

        sheet.freeze_panes = "A2"
        if title == "RSI Paper trades":
            sheet.auto_filter.ref = sheet.dimensions
        print(f"  {title}: {len(values)} row(s)")

    stamp = datetime.now().strftime("%Y-%m-%d")
    out = ROOT / "data" / f"RSI_CandlePattern_paper_book_{stamp}.xlsx"
    workbook.save(out)
    print(f"\nWrote {out}  ({out.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
