#!/usr/bin/env python3
"""Backfill RSI_CandlePattern entries missed between 1 Aug and 11 Sep 2026.

The live book ran with the old rule, which demanded a >=50% strong body on the
RSI 70/30 stretch bar. Under the corrected rule the stretch day only needs the
RSI tag. This opens every trade that rule change unlocks, replays targets and
stops forward on daily bars, and merges the result into the live ledger.

Existing live positions are never re-marked here: `book.update()` is fed a price
map that contains only the backfilled symbols, so the live legs pass through
untouched.
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import src.paper_trading.book as book_mod
from src.candle_patterns import (
    Candle,
    candle_stop_price,
    make_candle_alert,
    reversal_setup,
    same_day_setup,
)
from src.config import SignalType, load_config
from src.data.angelone_client import AngelOneClient
from src.data.option_expiry import expiry_entry_skip_reason
from src.indicators import calculate_rsi, calculate_smma
from src.oi_analyzer import no_short_skip_reason
from src.paper_trading.book import PaperBook

START = date(2026, 8, 1)
END = date(2026, 9, 11)
ENTRY_STAMP = "15:15:00"
LEDGER = ROOT / "data" / "rsi_candle_3m_2w_paper_book.json"
JOURNAL = ROOT / "data" / "rsi_candle_3m_2w_paper_trades.csv"
REPORT = ROOT / "data" / "backfill_aug1_report.json"


def set_clock(day: date, hhmm: str = ENTRY_STAMP) -> None:
    stamp = f"{day.isoformat()} {hhmm}"
    book_mod.now_stamp = lambda: stamp


def rsi_on(closes: list[float], period: int) -> float | None:
    if not closes:
        return None
    return calculate_rsi(pd.Series(closes, dtype=float), period=period)


def smma_on(closes: list[float], period: int) -> float | None:
    if not closes:
        return None
    return calculate_smma(pd.Series(closes, dtype=float), period=period)


def journal_day(raw: str) -> str:
    try:
        return datetime.strptime(str(raw).strip(), "%d-%b-%y").date().isoformat()
    except ValueError:
        return str(raw)[:10]


def busy_windows() -> dict[str, list[tuple[str, str]]]:
    """When each symbol was already held, from the live ledger and journal."""
    windows: dict[str, list[tuple[str, str]]] = {}
    if JOURNAL.exists():
        with JOURNAL.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                symbol = (row.get("Symbol") or "").strip()
                if not symbol:
                    continue
                start = journal_day(row.get("Entry date") or "")
                end = journal_day(row.get("Exit date") or "")
                windows.setdefault(symbol, []).append((start, end))
    book = json.loads(LEDGER.read_text(encoding="utf-8"))
    for position in book.get("positions") or []:
        if int(position.get("lots_open") or 0) <= 0:
            continue
        windows.setdefault(position["symbol"], []).append(
            (str(position["entry_time"])[:10], "9999-12-31")
        )
    return windows


def is_busy(windows: dict[str, list[tuple[str, str]]], symbol: str, day: str) -> bool:
    return any(start <= day <= end for start, end in windows.get(symbol, []))


def screen(client: AngelOneClient, config, symbols: list[str]) -> list[dict]:
    """Every new-rule signal between START and END, from daily cash bars."""
    cfg = config.candles
    call_th = config.rsi.call_threshold
    put_th = config.rsi.put_threshold
    period = config.rsi.period
    low, high = START.isoformat(), END.isoformat()
    found: list[dict] = []

    for index, symbol in enumerate(symbols, 1):
        if index % 50 == 0:
            print(f"  screened {index}/{len(symbols)}")
        try:
            rows = client.daily_full_ohlc(symbol)
        except Exception as exc:
            print(f"  {symbol}: daily OHLC failed ({exc})")
            continue
        bars = [
            Candle(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]))
            for r in rows
            if r[1] and float(r[1]) > 0
        ]
        if len(bars) < 20:
            continue
        closes = [bar.close for bar in bars]

        for position, today in enumerate(bars):
            if position < 1 or not (low <= today.date <= high):
                continue
            yesterday = bars[position - 1]
            prior = closes[:position]
            y_rsi = rsi_on(prior, period)

            setup = reversal_setup(
                yesterday,
                today,
                y_rsi,
                call_threshold=call_th,
                put_threshold=put_th,
                cfg=cfg,
            )
            same_day = False
            rsi_at_entry = y_rsi
            if setup:
                stop = candle_stop_price(
                    setup[0], reversal=today, prior=yesterday, same_day=False
                )
            else:
                rsi_close = rsi_on(prior + [today.close], period)
                rsi_high = rsi_on(prior + [today.high], period)
                rsi_low = rsi_on(prior + [today.low], period)
                setup = same_day_setup(
                    today,
                    rsi_at_close=rsi_close,
                    rsi_at_high=rsi_high,
                    rsi_at_low=rsi_low,
                    call_threshold=call_th,
                    put_threshold=put_th,
                    cfg=cfg,
                )
                if not setup:
                    continue
                same_day = True
                rsi_at_entry = rsi_close or rsi_high or rsi_low
                stop = candle_stop_price(
                    setup[0], reversal=today, prior=None, same_day=True
                )

            signal, pattern = setup
            if no_short_skip_reason(
                symbol,
                config.no_short_symbols,
                is_short=signal is SignalType.RSI_CANDLE_SHORT,
            ):
                continue
            found.append(
                {
                    "day": today.date,
                    "symbol": symbol,
                    "signal": signal,
                    "pattern": pattern,
                    "stop": stop,
                    "rsi": round(float(rsi_at_entry or 0.0), 2),
                    "kind": "same-day" if same_day else "next-day",
                }
            )

    found.sort(key=lambda row: (row["day"], row["symbol"]))
    return found


def main() -> None:
    config = load_config(ROOT / "config.yaml")
    paper = config.rsi_candle_2w_paper_trading
    client = AngelOneClient(
        rsi_period=config.rsi.period,
        history_days=config.data.history_days,
        extreme_history_days=config.data.extreme_history_days,
    )
    book = PaperBook(
        paper, path=LEDGER, journal=None, no_short_symbols=config.no_short_symbols
    )
    opening = {
        "realised": book.realised_pnl,
        "open": len([p for p in book.positions if p.is_open]),
        "margin": book.margin_blocked,
    }
    sessions_seen = set(book.session_dates)
    day_pnl_before = book.day_realised_pnl
    print(
        f"Live book: {opening['open']} open · realised ₹{opening['realised']:,.0f} · "
        f"margin ₹{opening['margin']:,.0f} · free ₹{book.free_capital:,.0f}"
    )

    try:
        symbols = client.fno_symbols()
        print(f"\nScreening {len(symbols)} F&O names {START} → {END} (new rule)…")
        signals = screen(client, config, symbols)
        print(f"  {len(signals)} raw signal(s)")

        windows = busy_windows()
        contracts: dict[str, object] = {}
        series: dict[str, list[tuple[str, float]]] = {}
        cash: dict[str, list[tuple[str, float]]] = {}
        added: list[dict] = []
        skipped: list[dict] = []
        exits: list[dict] = []
        backfilled: set[str] = set()

        by_day: dict[str, list[dict]] = {}
        for row in signals:
            by_day.setdefault(row["day"], []).append(row)

        def cash_closes(symbol: str) -> list[tuple[str, float]]:
            if symbol not in cash:
                cash[symbol] = [
                    (str(r[0])[:10], float(r[4]))
                    for r in client.daily_full_ohlc(symbol)
                    if r[4] and float(r[4]) > 0
                ]
            return cash[symbol]

        sessions = sorted(
            {
                stamp
                for symbol in symbols[:40]
                for stamp, _ in cash_closes(symbol)
                if START.isoformat() <= stamp <= END.isoformat()
            }
        )
        print(f"\nWalking {len(sessions)} session(s): exits first, then entries…")

        for day_s in sessions:
            day = date.fromisoformat(day_s)

            # ---- exits on backfilled positions carried in from earlier days ----
            set_clock(day, "15:15:00")
            marks: dict[str, float] = {}
            cash_stops: dict[str, float] = {}
            smma: dict[str, tuple[float | None, float | None]] = {}
            rsi: dict[str, float] = {}
            for position in book.positions:
                symbol = position.symbol
                if symbol not in backfilled or not position.is_open:
                    continue
                if position.entry_time[:10] >= day_s:
                    continue
                contract = contracts.get(symbol)
                if contract is None:
                    continue
                rows = series.get(f"{contract.exchange}:{contract.token}") or []
                prior = [close for stamp, close in rows if stamp <= day_s]
                if not prior:
                    continue
                mark = prior[-1]
                marks[symbol] = mark
                today_cash = [c for stamp, c in cash_closes(symbol) if stamp == day_s]
                if today_cash:
                    cash_stops[symbol] = today_cash[0]
                history = [
                    c for stamp, c in cash_closes(symbol) if stamp < day_s
                ] + [mark]
                smma[symbol] = (
                    smma_on(history, paper.smma_fast),
                    smma_on(history, paper.smma_slow),
                )
                value = rsi_on(history, config.rsi.period)
                if value is not None:
                    rsi[symbol] = value

            if marks:
                for event in book.update(
                    marks, day, rsi, smma, stop_prices=cash_stops or None
                ):
                    exits.append(
                        {
                            "day": day_s,
                            "symbol": event.symbol,
                            "kind": event.kind,
                            "detail": event.detail,
                            "pnl": round(event.pnl, 2),
                        }
                    )
                    print(
                        f"  {day_s} exit  {event.symbol:12} {event.kind:14} "
                        f"₹{event.pnl:>11,.0f}"
                    )
                # A fully closed name is free to signal again later in the window.
                live = {p.symbol for p in book.positions if p.is_open}
                for symbol in marks:
                    if symbol in live:
                        continue
                    spans = windows.get(symbol) or []
                    if spans and spans[-1][1] == "9999-12-31":
                        spans[-1] = (spans[-1][0], day_s)

            # ---- entries the old rule blocked ----
            reason = expiry_entry_skip_reason(day)
            for row in by_day.get(day_s, []):
                symbol = row["symbol"]
                if is_busy(windows, symbol, day_s):
                    continue
                if reason:
                    skipped.append({**row, "signal": row["signal"].value, "why": reason})
                    continue
                contract = client.futures_contract(
                    symbol, month_index=paper.futures_month, as_of=day
                )
                if not contract or not contract.token:
                    skipped.append(
                        {**row, "signal": row["signal"].value, "why": "no contract"}
                    )
                    continue
                key = f"{contract.exchange}:{contract.token}"
                if key not in series:
                    series[key] = client._fetch_futures_daily(contract)
                fills = [c for stamp, c in series[key] if stamp <= day_s]
                if not fills:
                    skipped.append(
                        {**row, "signal": row["signal"].value, "why": "no futures price"}
                    )
                    continue

                alert = make_candle_alert(
                    symbol=symbol,
                    ltp=fills[-1],
                    rsi=row["rsi"],
                    signal=row["signal"],
                    pattern=row["pattern"],
                    stop_price=row["stop"],
                )
                alert.expiry = contract.expiry
                alert.lot_size = contract.lot_size

                set_clock(day, "15:15:00")
                events = book.open_from_alerts([alert])
                entry = next((e for e in events if e.kind == "entry"), None)
                if entry is None:
                    why = events[0].detail if events else "no entry event"
                    skipped.append({**row, "signal": row["signal"].value, "why": why})
                    continue

                contracts[symbol] = contract
                backfilled.add(symbol)
                windows.setdefault(symbol, []).append((day_s, "9999-12-31"))
                added.append(
                    {
                        "day": day_s,
                        "symbol": symbol,
                        "signal": row["signal"].value,
                        "pattern": row["pattern"],
                        "kind": row["kind"],
                        "entry": round(fills[-1], 2),
                        "stop": None if row["stop"] is None else round(row["stop"], 2),
                        "lot_size": contract.lot_size,
                    }
                )
                print(
                    f"  {day_s} entry {symbol:12} {row['signal'].value:16} "
                    f"@ ₹{fills[-1]:,.2f} ({row['pattern']}, {row['kind']})"
                )

        print(f"\nOpened {len(added)} · skipped {len(skipped)} · exits {len(exits)}")
        if not added:
            print("Nothing to merge.")
            return

        # `_close_lots` rolls the book on the wall clock, so the replay can add
        # today's non-trading date to the session list. Put it back.
        book.session_dates = [d for d in book.session_dates if d in sessions_seen]
        book.day_date = END.isoformat()
        book.day_realised_pnl = day_pnl_before
        rows = book._pending_rows
        print(f"\n{len(rows)} closed leg(s) to append to the journal")
        if rows:
            existing = JOURNAL.read_text(encoding="utf-8").splitlines()
            header = existing[0].split(",") if existing else []
            with JOURNAL.open("a", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=header)
                for row in rows:
                    writer.writerow(row)
        book._pending_rows = []
        book.save()

        still_open = [p for p in book.positions if p.is_open]
        print(
            f"\nBook now: {len(still_open)} open (was {opening['open']}) · "
            f"realised ₹{book.realised_pnl:,.0f} (was ₹{opening['realised']:,.0f}) · "
            f"margin ₹{book.margin_blocked:,.0f} · free ₹{book.free_capital:,.0f}"
        )
        REPORT.write_text(
            json.dumps(
                {
                    "window": [START.isoformat(), END.isoformat()],
                    "opened": added,
                    "skipped": skipped,
                    "exits": exits,
                    "realised_before": opening["realised"],
                    "realised_after": round(book.realised_pnl, 2),
                    "open_before": opening["open"],
                    "open_after": len(still_open),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"Wrote {REPORT}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
