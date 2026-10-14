"""Public page for the four paper books. No keys, tokens, or sheet ids."""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

RECENT_EXITS = 20

BOOKS = (
    ("RSI Stretch", "data/rsi_stretch_paper_book.json", "data/rsi_stretch_paper_trades.csv"),
    ("RSI 3-lot", "data/rsi_candle_3lot_paper_book.json", "data/rsi_candle_3lot_paper_trades.csv"),
    ("RSI candle", "data/rsi_candle_3m_2w_paper_book.json", "data/rsi_candle_3m_2w_paper_trades.csv"),
    ("Heikin Ashi", "data/heikin_ashi_paper_book.json", "data/heikin_ashi_paper_trades.csv"),
)


def build_desk(root: Path, books: tuple = BOOKS) -> dict:
    return {
        "generated_at": datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M IST"),
        "books": [_book(root, name, ledger, journal) for name, ledger, journal in books],
    }


def _book(root: Path, name: str, ledger_rel: str, journal_rel: str) -> dict:
    path = root / ledger_rel
    empty = {
        "name": name,
        "saved": False,
        "capital": None,
        "realised": None,
        "margin": None,
        "open_count": 0,
        "open_lots": 0,
        "closed_count": 0,
        "updated_at": None,
        "unrealised": None,
        "open": [],
        "recent_exits": [],
    }
    if not path.exists():
        return empty
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty

    open_rows = []
    margin = 0.0
    lots = 0
    for position in raw.get("positions") or []:
        if not isinstance(position, dict) or int(position.get("lots_open") or 0) <= 0:
            continue
        lots_open = int(position["lots_open"])
        lots += lots_open
        margin += float(position.get("margin_blocked") or 0)
        booked = 0.0
        for leg in position.get("closed_legs") or []:
            if isinstance(leg, dict):
                booked += float(leg.get("pnl") or 0)
        open_rows.append(
            {
                "symbol": position.get("symbol"),
                "direction": position.get("direction"),
                "lots_open": lots_open,
                "lots_total": int(position.get("lots_total") or lots_open),
                "entry_price": position.get("entry_price"),
                "entry_time": position.get("entry_time"),
                "expiry": position.get("expiry"),
                "booked": round(booked, 2),
            }
        )
    open_rows.sort(key=lambda row: str(row.get("entry_time") or ""), reverse=True)
    realised = raw.get("realised_pnl")
    return {
        "name": name,
        "saved": True,
        "capital": raw.get("capital"),
        "realised": None if realised is None else float(realised),
        "unrealised": _open_pnl(raw.get("positions") or [], raw.get("last_marks") or {}),
        "margin": round(margin, 2),
        "open_count": len(open_rows),
        "open_lots": lots,
        "closed_count": int(raw.get("closed_count") or 0),
        "updated_at": raw.get("updated_at"),
        "open": open_rows,
        "recent_exits": _recent_exits(root / journal_rel),
    }


def _open_pnl(positions: list, marks: dict) -> float | None:
    """Mark still-open lots from the last saved futures price."""
    total = 0.0
    saw_open = False
    for position in positions:
        if not isinstance(position, dict) or int(position.get("lots_open") or 0) <= 0:
            continue
        saw_open = True
        price = marks.get(position.get("symbol"))
        lot_size = position.get("lot_size")
        entry = position.get("entry_price")
        if price in (None, "") or not lot_size or entry in (None, ""):
            return None
        difference = float(price) - float(entry)
        if position.get("direction") == "SHORT":
            difference = -difference
        total += difference * int(lot_size) * int(position["lots_open"])
    if not saw_open:
        return 0.0
    return round(total, 2)


def _recent_exits(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except OSError:
        return []
    exits = []
    for row in rows[-RECENT_EXITS:]:
        exits.append(
            {
                "symbol": row.get("Symbol") or "",
                "side": row.get("Buy/Sell") or "",
                "entry_date": row.get("Entry date") or "",
                "exit_date": row.get("Exit date") or "",
                "exit_price": row.get("Exit price") or "",
                "pnl": _number(row.get("Profit/loss")),
                "reason": row.get("Exit reason") or "",
            }
        )
    exits.reverse()
    return exits


def _number(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None
