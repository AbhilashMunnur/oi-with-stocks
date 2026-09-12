#!/usr/bin/env python3
"""Add the 'Exit trigger' column to the existing RSI journal and sheet.

New legs record their trigger live. Rows booked before the column existed get
one reconstructed from the entry/exit prices: a fill sitting on the 5%/12%
level means the percent target won the race, anything else means the SMMA line
did. Stops carry a candle stop unless the fill lands on the fixed percent stop.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

import gspread

from src.config import load_config
from src.paper_trading.journal import COLUMNS, TradeJournal

JOURNAL = ROOT / "data" / "rsi_candle_3m_2w_paper_trades.csv"
TOLERANCE = 0.25  # percentage points


def move_pct(side: str, entry: float, exit_: float) -> float:
    if entry <= 0:
        return 0.0
    if side == "Buy":
        return (exit_ - entry) / entry * 100.0
    return (entry - exit_) / entry * 100.0


def reconstruct(row: dict, paper) -> str:
    reason = (row.get("Exit reason") or "").strip()
    side = (row.get("Buy/Sell") or "").strip()
    try:
        entry = float(row["Entry price"])
        exit_ = float(row["Exit price"])
    except (KeyError, ValueError):
        return ""
    move = move_pct(side, entry, exit_)
    near = lambda target: abs(move - target) <= TOLERANCE

    if reason == "first_target":
        if near(paper.first_target_pct):
            return f"lot 1 booked — {paper.first_target_pct:g}% target ₹{exit_:,.2f}"
        return f"lot 1 booked — SMMA {paper.smma_fast} ₹{exit_:,.2f}"
    if reason == "second_target":
        if near(paper.second_target_pct):
            return f"final lot booked — {paper.second_target_pct:g}% target ₹{exit_:,.2f}"
        return f"final lot booked — SMMA {paper.smma_slow} ₹{exit_:,.2f}"
    if reason == "rsi_target":
        threshold = (
            paper.second_lot_rsi_short if side == "Sell" else paper.second_lot_rsi_long
        )
        return f"final lot booked — RSI reached {threshold:g}"
    if reason == "stop_loss":
        # Every RSI_Candle entry stores the reversal bar as its stop, so the
        # fixed percent stop can never fire on this book — a percent-looking
        # move is a coincidence, not the trigger.
        edge = "high" if side == "Sell" else "low"
        return (
            f"candle stop — cash closed through the entry-candle {edge}, "
            f"filled ₹{exit_:,.2f} ({move:+.1f}%)"
        )
    if reason == "expiry":
        return "contract expiry"
    return reason.replace("_", " ") if reason else ""


def main() -> None:
    config = load_config(ROOT / "config.yaml")
    paper = config.rsi_candle_2w_paper_trading

    force = "--force" in sys.argv
    rows = list(csv.DictReader(JOURNAL.open(newline="", encoding="utf-8")))
    filled = 0
    for row in rows:
        if force or not (row.get("Exit trigger") or "").strip():
            row["Exit trigger"] = reconstruct(row, paper)
            filled += 1
    print(f"{len(rows)} row(s); reconstructed {filled} trigger(s)")

    with JOURNAL.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"rewrote {JOURNAL.name} with {len(COLUMNS)} columns")

    journal = TradeJournal(JOURNAL, sheet_id=paper.google_sheet_id)
    gc = gspread.authorize(journal._credentials())
    spreadsheet = gc.open_by_key(paper.google_sheet_id)
    sheet = spreadsheet.worksheet(paper.google_worksheet)
    sheet.clear()
    sheet.resize(rows=max(len(rows) + 20, 200), cols=len(COLUMNS))
    sheet.update(
        [COLUMNS] + [[row.get(column, "") for column in COLUMNS] for row in rows],
        "A1",
    )
    print(f"'{paper.google_worksheet}': rewrote {len(rows)} leg(s) with Exit trigger")

    from collections import Counter

    print("\nsample triggers:")
    for text, count in Counter(r["Exit trigger"][:60] for r in rows).most_common(8):
        print(f"  {count:4}  {text}")


if __name__ == "__main__":
    main()
