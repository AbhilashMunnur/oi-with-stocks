#!/usr/bin/env python3
"""Push the backfilled RSI_CandlePattern book to Google Sheets and Telegram.

Rewrites the closed-trades tab from the CSV (the backfill inserted rows the
sheet has never seen), appends a fresh portfolio snapshot, and sends the
positions dashboard as a PNG.
"""

from __future__ import annotations

import csv
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from src.config import load_config
from src.data.angelone_client import AngelOneClient
from src.notifications.notifier import Notifier
from src.paper_trading.book import PaperBook
from src.paper_trading.journal import COLUMNS, SUMMARY_COLUMNS, TradeJournal

LEDGER = ROOT / "data" / "rsi_candle_3m_2w_paper_book.json"
JOURNAL = ROOT / "data" / "rsi_candle_3m_2w_paper_trades.csv"


def main() -> None:
    config = load_config(ROOT / "config.yaml")
    paper = config.rsi_candle_2w_paper_trading
    journal = TradeJournal(
        JOURNAL,
        sheet_id=paper.google_sheet_id,
        worksheet=paper.google_worksheet,
        summary_worksheet=paper.google_summary_worksheet,
    )
    book = PaperBook(
        paper, path=LEDGER, journal=journal, no_short_symbols=config.no_short_symbols
    )
    opens = [p for p in book.positions if p.is_open]
    print(f"{len(opens)} open · realised ₹{book.realised_pnl:,.0f}")

    client = AngelOneClient(
        rsi_period=config.rsi.period,
        history_days=config.data.history_days,
        extreme_history_days=config.data.extreme_history_days,
    )
    try:
        pairs = [(p.symbol, p.expiry) for p in opens if p.expiry]
        marks = client.get_futures_ltps_for_expiries(pairs)
        missing = [p.symbol for p in opens if p.symbol not in marks]
        if missing:
            marks.update(client.get_futures_ltps(missing, month_index=paper.futures_month))
        for position in opens:
            marks.setdefault(position.symbol, position.entry_price)
        print(f"marked {len(marks)} name(s)")
    finally:
        client.close()

    unrealised = book.unrealised(marks)
    print(
        f"realised ₹{book.realised_pnl:,.0f} · unrealised ₹{unrealised:,.0f} · "
        f"gross ₹{book.realised_pnl + unrealised:,.0f} · "
        f"margin ₹{book.margin_blocked:,.0f} · free ₹{book.free_capital:,.0f}"
    )

    # ---- closed-trades tab: full rewrite from the CSV ----
    import gspread

    rows = list(csv.DictReader(JOURNAL.open(newline="", encoding="utf-8")))
    gc = gspread.authorize(journal._credentials())
    spreadsheet = gc.open_by_key(paper.google_sheet_id)
    sheet = journal._ensure_worksheet(spreadsheet, paper.google_worksheet, COLUMNS)
    sheet.clear()
    sheet.resize(rows=max(len(rows) + 20, 200), cols=len(COLUMNS))
    sheet.update(
        [COLUMNS] + [[row.get(column, "") for column in COLUMNS] for row in rows],
        "A1",
    )
    print(f"  '{paper.google_worksheet}': rewrote {len(rows)} closed leg(s)")

    # ---- portfolio summary snapshot ----
    summary = spreadsheet.worksheet(paper.google_summary_worksheet)
    row = {
        "Date": datetime.now().strftime("%d-%b-%y"),
        "Time": datetime.now().strftime("%H:%M"),
        "Total number of positions taken": len(opens),
        "Capital used in positions": round(book.margin_blocked),
        "Profit or loss": round(book.realised_pnl + unrealised),
        "Total realised profit or loss": round(book.realised_pnl),
        "Unrealised profit or loss": round(unrealised),
    }
    summary.append_row([row[column] for column in SUMMARY_COLUMNS])
    print(f"  '{paper.google_summary_worksheet}': appended snapshot")

    # ---- Telegram dashboard (picture format) ----
    notifier = Notifier(config.notifications)
    image = book.telegram_dashboard_image(marks, events=[], closing=True)
    caption = book.telegram_report(marks, events=[], closing=True)
    delivered = notifier.send_photo(image, caption=caption, parse_mode="HTML")
    print(f"  Telegram dashboard sent to {delivered} recipient(s)")


if __name__ == "__main__":
    main()
