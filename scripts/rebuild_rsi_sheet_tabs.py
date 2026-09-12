#!/usr/bin/env python3
"""Rebuild the RSI_CandlePattern reporting tabs after the 1 Aug backfill.

Rewrites three tabs from the journal CSV and the live ledger:
  * RSI Portfolio Summary  — one end-of-day row per session, in the same seven
    columns the scanner appends, so live snapshots keep lining up.
  * RSI P&L from Aug 1     — daily entries / exits / realised / cumulative.
  * RSI Charges estimate   — per-leg Indian F&O cost stack.
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

import gspread

from src.config import load_config
from src.data.angelone_client import AngelOneClient
from src.paper_trading.book import PaperBook
from src.paper_trading.journal import SUMMARY_COLUMNS, TradeJournal

LEDGER = ROOT / "data" / "rsi_candle_3m_2w_paper_book.json"
JOURNAL = ROOT / "data" / "rsi_candle_3m_2w_paper_trades.csv"
START = "2026-08-01"

# Indian F&O cost stack (stock futures).
BROKERAGE_PER_ORDER = 20.0
STT_SELL = 0.0002        # 0.02% on the sell leg
STAMP_BUY = 0.00002      # 0.002% on the buy leg
EXCHANGE_TXN = 0.0000188  # ~0.00188% of turnover
SEBI_FEE = 0.000001      # Rs 10 per crore
CLEARING = 0.000005      # ~0.0005%
GST = 0.18               # on brokerage + exchange + SEBI + clearing

P_AND_L_TAB = "RSI P&L from Aug 1"
OLD_P_AND_L_TAB = "RSI P&L from Aug 7"
CHARGES_TAB = "RSI Charges estimate"


def iso(raw: str) -> str:
    try:
        return datetime.strptime(str(raw).strip(), "%d-%b-%y").date().isoformat()
    except ValueError:
        return str(raw)[:10]


def parse_capital(raw: str) -> tuple[float, int]:
    """'185300*2' -> (margin per lot, lots)."""
    text = str(raw or "").strip()
    if "*" in text:
        left, right = text.split("*", 1)
        try:
            return float(left), int(right)
        except ValueError:
            return 0.0, 0
    try:
        return float(text), 1
    except ValueError:
        return 0.0, 0


def main() -> None:
    config = load_config(ROOT / "config.yaml")
    paper = config.rsi_candle_2w_paper_trading
    journal = TradeJournal(JOURNAL, sheet_id=paper.google_sheet_id)
    book = PaperBook(
        paper, path=LEDGER, journal=None, no_short_symbols=config.no_short_symbols
    )
    opens = [p for p in book.positions if p.is_open]

    client = AngelOneClient(
        rsi_period=config.rsi.period,
        history_days=config.data.history_days,
        extreme_history_days=config.data.extreme_history_days,
    )
    try:
        marks = client.get_futures_ltps_for_expiries(
            [(p.symbol, p.expiry) for p in opens if p.expiry]
        )
        missing = [p.symbol for p in opens if p.symbol not in marks]
        if missing:
            marks.update(
                client.get_futures_ltps(missing, month_index=paper.futures_month)
            )
    finally:
        client.close()
    for position in opens:
        marks.setdefault(position.symbol, position.entry_price)
    unrealised_now = book.unrealised(marks)

    rows = list(csv.DictReader(JOURNAL.open(newline="", encoding="utf-8")))
    legs = []
    for row in rows:
        margin_per_lot, lots = parse_capital(row.get("Capital needed"))
        entry_price = float(row["Entry price"])
        lot_size = (
            margin_per_lot / (paper.margin_pct / 100.0 * entry_price)
            if entry_price > 0 and margin_per_lot > 0
            else 0.0
        )
        legs.append(
            {
                "symbol": row["Symbol"],
                "side": row["Buy/Sell"],
                "entry_day": iso(row["Entry date"]),
                "exit_day": iso(row["Exit date"]),
                "entry": entry_price,
                "exit": float(row["Exit price"]),
                "lots": lots,
                "margin_per_lot": margin_per_lot,
                "qty": lots * round(lot_size),
                "pnl": float(row["Profit/loss"]),
                "reason": row["Exit reason"],
            }
        )

    # ---- per-position bookkeeping so we can count open names per day ----
    positions: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"lots": 0, "margin": 0.0, "closes": []}
    )
    for leg in legs:
        key = (leg["symbol"], leg["entry_day"])
        positions[key]["lots"] += leg["lots"]
        positions[key]["margin"] = leg["margin_per_lot"]
        positions[key]["closes"].append((leg["exit_day"], leg["lots"]))
    for position in opens:
        key = (position.symbol, position.entry_time[:10])
        positions[key]["lots"] += position.lots_open
        positions[key]["margin"] = (
            position.margin_blocked / max(position.lots_open, 1)
        )

    sessions = sorted(
        {leg["entry_day"] for leg in legs}
        | {leg["exit_day"] for leg in legs}
        | {p.entry_time[:10] for p in opens}
    )
    sessions = [day for day in sessions if day >= START]

    # The CSV was truncated on 3 Sep and lost 41 early legs. The ledger kept
    # counting them, so seed the running total with that difference to make the
    # sheet close on the ledger's realised P&L.
    journal_realised = sum(leg["pnl"] for leg in legs)
    missing_legs = round(book.realised_pnl - journal_realised, 2)

    daily = []
    cumulative = missing_legs
    for day in sessions:
        entries = sum(1 for key in positions if key[1] == day)
        day_legs = [leg for leg in legs if leg["exit_day"] == day]
        day_pnl = sum(leg["pnl"] for leg in day_legs)
        cumulative += day_pnl

        open_names = 0
        capital_used = 0.0
        for (symbol, entry_day), data in positions.items():
            if entry_day > day:
                continue
            closed = sum(lots for stamp, lots in data["closes"] if stamp <= day)
            left = data["lots"] - closed
            if left > 0:
                open_names += 1
                capital_used += data["margin"] * left
        daily.append(
            {
                "date": day,
                "entries": entries,
                "exit_legs": len(day_legs),
                "day_pnl": day_pnl,
                "cumulative": cumulative,
                "open_names": open_names,
                "capital_used": capital_used,
            }
        )

    gc = gspread.authorize(journal._credentials())
    spreadsheet = gc.open_by_key(paper.google_sheet_id)

    def fresh(title: str, cols: int, rows_needed: int):
        try:
            sheet = spreadsheet.worksheet(title)
            sheet.clear()
            sheet.resize(rows=max(rows_needed + 20, 60), cols=cols)
        except gspread.WorksheetNotFound:
            sheet = spreadsheet.add_worksheet(
                title, rows=max(rows_needed + 20, 60), cols=cols
            )
        return sheet

    # ---- 1. Portfolio summary, scanner-compatible columns ----
    summary_rows = [SUMMARY_COLUMNS]
    for row in daily:
        stamp = datetime.strptime(row["date"], "%Y-%m-%d").strftime("%d-%b-%y")
        last = row is daily[-1]
        summary_rows.append(
            [
                stamp,
                "15:30",
                row["open_names"],
                round(row["capital_used"]),
                round(row["cumulative"] + (unrealised_now if last else 0)),
                round(row["cumulative"]),
                round(unrealised_now) if last else "",
            ]
        )
    sheet = fresh(paper.google_summary_worksheet, len(SUMMARY_COLUMNS), len(summary_rows))
    sheet.update(summary_rows, "A1")
    print(
        f"'{paper.google_summary_worksheet}': {len(summary_rows) - 1} daily row(s) "
        f"{daily[0]['date']} → {daily[-1]['date']}"
    )

    # ---- 2. Daily P&L tab ----
    wins = [leg for leg in legs if leg["pnl"] > 0]
    losses = [leg for leg in legs if leg["pnl"] <= 0]
    realised = book.realised_pnl
    pnl_rows = [
        ["RSI Candle paper — P&L from 1 Aug 2026", "", "", "", "", ""],
        [f"Rebuilt {datetime.now():%d-%b-%Y %H:%M} · unrealised marked live", "", "", "", "", ""],
        [],
        ["Date", "Entries taken", "Legs closed", "Day P&L", "Cumulative realised", "Open names EOD"],
        ["Opening", "", "", round(missing_legs), round(missing_legs), ""],
    ]
    for row in daily:
        pnl_rows.append(
            [
                datetime.strptime(row["date"], "%Y-%m-%d").strftime("%d-%b-%y"),
                row["entries"],
                row["exit_legs"],
                round(row["day_pnl"]),
                round(row["cumulative"]),
                row["open_names"],
            ]
        )
    pnl_rows += [
        [],
        ["Totals", "", "", "", "", ""],
        ["Closed legs in journal", len(legs), "", "", "", ""],
        ["Winning legs", len(wins), "", round(sum(l["pnl"] for l in wins)), "", ""],
        ["Losing legs", len(losses), "", round(sum(l["pnl"] for l in losses)), "", ""],
        ["Hit rate", f"{len(wins) / max(len(legs), 1):.1%}", "", "", "", ""],
        [],
        ["Realised from journal legs", "", "", round(journal_realised), "", ""],
        ["Earlier legs lost when the CSV was truncated on 3 Sep", "", "", round(missing_legs), "", ""],
        ["Realised P&L (ledger)", "", "", round(realised), "", ""],
        ["Unrealised P&L (live)", "", "", round(unrealised_now), "", ""],
        ["Gross P&L", "", "", round(realised + unrealised_now), "", ""],
        [],
        ["Open positions now", len(opens), "", "", "", ""],
        ["Margin blocked", round(book.margin_blocked), "", "", "", ""],
        ["Free capital", round(book.free_capital), "", "", "", ""],
    ]
    sheet = fresh(P_AND_L_TAB, 6, len(pnl_rows))
    sheet.update(pnl_rows, "A1")
    print(f"'{P_AND_L_TAB}': {len(daily)} daily row(s), realised ₹{realised:,.0f}")
    try:
        spreadsheet.del_worksheet(spreadsheet.worksheet(OLD_P_AND_L_TAB))
        print(f"  removed stale '{OLD_P_AND_L_TAB}'")
    except gspread.WorksheetNotFound:
        pass

    # ---- 3. Charges estimate ----
    totals = defaultdict(float)
    for leg in legs:
        buy_value = leg["entry"] * leg["qty"] if leg["side"] == "Buy" else leg["exit"] * leg["qty"]
        sell_value = leg["exit"] * leg["qty"] if leg["side"] == "Buy" else leg["entry"] * leg["qty"]
        turnover = buy_value + sell_value
        brokerage = BROKERAGE_PER_ORDER * 2
        exchange = turnover * EXCHANGE_TXN
        sebi = turnover * SEBI_FEE
        clearing = turnover * CLEARING
        totals["turnover"] += turnover
        totals["brokerage"] += brokerage
        totals["stt"] += sell_value * STT_SELL
        totals["stamp"] += buy_value * STAMP_BUY
        totals["exchange"] += exchange
        totals["sebi"] += sebi
        totals["clearing"] += clearing
        totals["gst"] += (brokerage + exchange + sebi + clearing) * GST

    charge_total = sum(
        totals[k] for k in ("brokerage", "stt", "stamp", "exchange", "sebi", "clearing", "gst")
    )
    charge_rows = [
        ["RSI Candle — estimated trading charges", "", ""],
        [f"{len(legs)} closed legs from 1 Aug 2026 · {len(legs) * 2} orders", "", ""],
        [],
        ["Component", "Basis", "Amount (Rs)"],
        ["Brokerage", f"Rs {BROKERAGE_PER_ORDER:.0f} x {len(legs) * 2} orders", round(totals["brokerage"])],
        ["STT", f"{STT_SELL:.4%} of sell turnover", round(totals["stt"])],
        ["Stamp duty", f"{STAMP_BUY:.4%} of buy turnover", round(totals["stamp"])],
        ["Exchange txn", f"{EXCHANGE_TXN:.5%} of turnover", round(totals["exchange"])],
        ["SEBI fee", "Rs 10 per crore", round(totals["sebi"])],
        ["Clearing", f"{CLEARING:.5%} of turnover", round(totals["clearing"])],
        ["GST", "18% on brokerage + exchange + SEBI + clearing", round(totals["gst"])],
        [],
        ["Total charges", "", round(charge_total)],
        ["Total turnover", "", round(totals["turnover"])],
        ["Charges as % of turnover", "", f"{charge_total / max(totals['turnover'], 1):.4%}"],
        [],
        ["Realised P&L (ledger, gross)", "", round(realised)],
        ["Realised P&L (net of charges)", "", round(realised - charge_total)],
        ["Unrealised P&L (live, gross)", "", round(unrealised_now)],
        ["Gross P&L net of charges", "", round(realised + unrealised_now - charge_total)],
        [],
        ["Note", "Estimate only — actual broker rates and slabs will differ.", ""],
        ["Note", f"Charges cover the {len(legs)} legs still in the journal; the "
                 "41 legs lost in the 3 Sep truncation are not costed.", ""],
    ]
    sheet = fresh(CHARGES_TAB, 3, len(charge_rows))
    sheet.update(charge_rows, "A1")
    print(f"'{CHARGES_TAB}': total ₹{charge_total:,.0f} on ₹{totals['turnover']:,.0f} turnover")
    print(f"\nNet of charges: realised ₹{realised - charge_total:,.0f} · "
          f"gross ₹{realised + unrealised_now - charge_total:,.0f}")


if __name__ == "__main__":
    main()
