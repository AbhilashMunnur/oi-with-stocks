#!/usr/bin/env python3
"""Seed a paper book from 1 Jul 2026 and replace its Google Sheet tabs.

``--book 3lot`` (default) is the final RSI + normal-candle book;
``--book heikin_ashi`` is the Heikin_Ashi book (same ladder, HA entries).

Uses the live PaperBook rules from config.yaml (flat percent stop, the
book's own RSI entry gate, 5%/12% or SMMA 21/50, runner on RSI 30/70 or a
strong close through SMMA 21). Signals are screened at the global 70/30
exactly like the scanner; the book then applies its rsi_call_threshold /
rsi_put_threshold before opening, so this replay matches live. Daily cash close
stands in for the 15:15 3rd-month futures fill — the same basis as the
year backtests. Historical 30-minute marks are not available; one portfolio
snapshot is written at 15:15 on each session. Live scans keep appending
every half hour after this seed.

Does not touch the live RSI_CandlePattern ledger.

    .venv/bin/python scripts/seed_3lot_from_july.py
    .venv/bin/python scripts/seed_3lot_from_july.py --book heikin_ashi
    .venv/bin/python scripts/seed_3lot_from_july.py --skip-sheets
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

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
from src.data.option_expiry import expiry_entry_skip_reason, last_tuesday
from src.heikin_ashi import ha_reversal_setup, heikin_ashi, make_ha_alert
from src.indicators import calculate_rsi_series, calculate_smma_series
from src.oi_analyzer import no_short_skip_reason
from src.paper_trading.book import PaperBook
from src.paper_trading.futures_expiry import target_futures_year_month
from src.paper_trading.journal import COLUMNS, SUMMARY_COLUMNS, TradeJournal

PERIOD = 14
START = "2026-07-01"
CACHE = ROOT / ".cache"
OHLC_CACHE = CACHE / "seed_3lot_ohlc.json"


def set_clock(day: str, hhmm: str) -> None:
    book_mod.now_stamp = lambda: f"{day} {hhmm}"


def wilder(closes: pd.Series, period: int = PERIOD):
    delta = closes.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    avg_gain = gains.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = losses.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    return avg_gain, avg_loss


def rsi_at(
    avg_gain: float,
    avg_loss: float,
    prev_close: float,
    price: float,
    period: int = PERIOD,
) -> float | None:
    if pd.isna(avg_gain) or pd.isna(avg_loss):
        return None
    delta = price - prev_close
    gain = max(delta, 0.0)
    loss = max(-delta, 0.0)
    up = (avg_gain * (period - 1) + gain) / period
    down = (avg_loss * (period - 1) + loss) / period
    if down == 0:
        return 100.0
    return 100 - 100 / (1 + up / down)


def futures_expiry(as_of: date) -> str:
    year, month = target_futures_year_month(as_of, 3)
    return last_tuesday(year, month).isoformat()


def load_bars(client: AngelOneClient, symbols: list[str], days: int) -> dict[str, list]:
    CACHE.mkdir(parents=True, exist_ok=True)
    if OHLC_CACHE.exists():
        data = json.loads(OHLC_CACHE.read_text())
        print(f"  reusing cached bars for {len(data)} name(s)")
        return data

    data: dict[str, list] = {}
    for index, symbol in enumerate(symbols, 1):
        rows = client._request_candles(symbol, days=days)
        kept = [
            [str(row[0])[:10], float(row[1]), float(row[2]), float(row[3]), float(row[4])]
            for row in rows or []
            if len(row) >= 5 and row[1] and float(row[1]) > 0
        ]
        if len(kept) > 80:
            data[symbol] = kept
        if index % 25 == 0:
            print(f"  fetched {index}/{len(symbols)}")
            OHLC_CACHE.write_text(json.dumps(data))
    OHLC_CACHE.write_text(json.dumps(data))
    print(f"  cached {len(data)} name(s) → {OHLC_CACHE}")
    return data


def build_signals(data: dict[str, list], config, lot_sizes: dict[str, int]) -> list[dict]:
    cfg = config.candles
    call_th, put_th = config.rsi.call_threshold, config.rsi.put_threshold
    out: list[dict] = []

    for symbol, rows in data.items():
        closes = pd.Series([row[4] for row in rows], dtype=float)
        rsi_series = calculate_rsi_series(closes, PERIOD)
        if rsi_series is None:
            continue
        avg_gain, avg_loss = wilder(closes, PERIOD)

        for i in range(PERIOD + 2, len(rows)):
            prev, cur = rows[i - 1], rows[i]
            yesterday = Candle(prev[0], prev[1], prev[2], prev[3], prev[4])
            today = Candle(cur[0], cur[1], cur[2], cur[3], cur[4])
            y_rsi = rsi_series.iloc[i - 1]
            y_rsi = None if pd.isna(y_rsi) else float(y_rsi)

            setup = reversal_setup(
                yesterday,
                today,
                y_rsi,
                call_threshold=call_th,
                put_threshold=put_th,
                cfg=cfg,
            )
            rsi_val = y_rsi
            if setup:
                stop = candle_stop_price(
                    setup[0], reversal=today, prior=yesterday, same_day=False
                )
            else:
                state = (avg_gain.iloc[i - 1], avg_loss.iloc[i - 1], prev[4])
                rsi_close = rsi_at(*state, today.close)
                rsi_high = rsi_at(*state, today.high)
                rsi_low = rsi_at(*state, today.low)
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
                rsi_val = rsi_close or rsi_high or rsi_low
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
            if lot_sizes.get(symbol, 0) <= 0:
                continue
            out.append(
                {
                    "day": cur[0],
                    "symbol": symbol,
                    "signal": signal.value,
                    "pattern": pattern,
                    "stop": stop,
                    "entry": cur[4],
                    "rsi": round(float(rsi_val or 0), 2),
                }
            )
    out.sort(key=lambda row: (row["day"], row["symbol"]))
    return out


def build_ha_signals(data: dict[str, list], config, lot_sizes: dict[str, int]) -> list[dict]:
    """Heikin_Ashi entries, one per (day, symbol), mirroring the live screen.

    Today's daily close stands in for the 15:15 live price, so today's HA
    candle and normal candle are the completed bars. The RSI window is the
    last ``rsi_lookback_sessions`` finished days plus today.
    """
    ha_cfg = config.heikin_ashi
    call_th, put_th = config.rsi.call_threshold, config.rsi.put_threshold
    window = 40  # enough history for the weak run walk-back
    out: list[dict] = []

    for symbol, rows in data.items():
        if lot_sizes.get(symbol, 0) <= 0:
            continue
        bars = [Candle(r[0], r[1], r[2], r[3], r[4]) for r in rows]
        ha = heikin_ashi(bars)
        closes = pd.Series([b.close for b in bars], dtype=float)
        rsi_series = calculate_rsi_series(closes, PERIOD)
        if rsi_series is None:
            continue
        for i in range(PERIOD + 3, len(bars)):
            lo = max(0, i - ha_cfg.rsi_lookback_sessions)
            recent = [
                None if pd.isna(v) else float(v)
                for v in rsi_series.iloc[lo : i + 1]
            ]
            setup = ha_reversal_setup(
                bars[max(0, i - window) : i + 1],
                ha[max(0, i - window) : i + 1],
                recent,
                call_threshold=call_th,
                put_threshold=put_th,
                ha_cfg=ha_cfg,
                candle_cfg=config.candles,
            )
            if not setup:
                continue
            signal, pattern, stretch = setup
            if no_short_skip_reason(
                symbol,
                config.no_short_symbols,
                is_short=signal is SignalType.HA_SHORT,
            ):
                continue
            out.append(
                {
                    "day": bars[i].date,
                    "symbol": symbol,
                    "signal": signal.value,
                    "pattern": pattern,
                    "stop": None,
                    "entry": bars[i].close,
                    "rsi": round(stretch, 2),
                }
            )
    out.sort(key=lambda row: (row["day"], row["symbol"]))
    return out


BOOKS = {
    "3lot": ("rsi_candle_3lot_paper_trading", build_signals),
    "heikin_ashi": ("heikin_ashi_paper_trading", build_ha_signals),
}


def alert_for(row: dict):
    signal = SignalType(row["signal"])
    if signal in (SignalType.HA_SHORT, SignalType.HA_LONG):
        return make_ha_alert(
            symbol=row["symbol"],
            ltp=row["entry"],
            rsi=row["rsi"],
            signal=signal,
            pattern=row["pattern"],
        )
    return make_candle_alert(
        symbol=row["symbol"],
        ltp=row["entry"],
        rsi=row["rsi"],
        signal=signal,
        pattern=row["pattern"],
        stop_price=None,
    )


def write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=220)
    parser.add_argument("--start", default=START)
    parser.add_argument("--skip-sheets", action="store_true")
    parser.add_argument(
        "--book",
        choices=sorted(BOOKS),
        default="3lot",
        help="Which book to seed: 3lot (RSI + normal candle) or heikin_ashi",
    )
    args = parser.parse_args()

    config = load_config(ROOT / "config.yaml")
    attr, signal_builder = BOOKS[args.book]
    paper = getattr(config, attr)
    if paper is None or not paper.enabled:
        raise SystemExit(f"{attr} is not enabled")

    client = AngelOneClient(
        rsi_period=config.rsi.period,
        history_days=config.data.history_days,
        extreme_history_days=config.data.extreme_history_days,
    )
    try:
        symbols = client.fno_symbols()
        print(f"Loading {args.days}d of daily bars for {len(symbols)} F&O names…")
        data = load_bars(client, symbols, args.days)
        lot_sizes = {symbol: client.lot_size(symbol) or 0 for symbol in data}
    finally:
        client.close()

    sessions = sorted({row[0] for rows in data.values() for row in rows})
    trade_days = [day for day in sessions if day >= args.start]
    if not trade_days:
        raise SystemExit(f"no sessions on or after {args.start}")
    print(
        f"  {len(data)} names, trading {trade_days[0]} → {trade_days[-1]} "
        f"({len(trade_days)} sessions)"
    )

    signals = [
        row for row in signal_builder(data, config, lot_sizes) if row["day"] >= args.start
    ]
    print(f"  {len(signals)} qualifying signal(s)")

    ledger = ROOT / paper.ledger_path
    journal_csv = ROOT / paper.journal_csv
    if ledger.exists():
        ledger.unlink()
    if journal_csv.exists():
        journal_csv.unlink()

    book = PaperBook(
        paper,
        path=ledger,
        journal=None,
        no_short_symbols=config.no_short_symbols,
        candle_cfg=config.candles,
    )

    closes = {symbol: {row[0]: row[4] for row in rows} for symbol, rows in data.items()}
    frames: dict[str, dict] = {}
    for symbol, rows in data.items():
        series = pd.Series([row[4] for row in rows], dtype=float)
        frames[symbol] = {
            "dates": [row[0] for row in rows],
            "rsi": calculate_rsi_series(series, PERIOD),
            "fast": calculate_smma_series(series, paper.smma_fast or 21),
            "slow": calculate_smma_series(series, paper.smma_slow or 50),
        }
    index_of = {
        symbol: {day: i for i, day in enumerate(frame["dates"])}
        for symbol, frame in frames.items()
    }
    by_day: dict[str, list[dict]] = {}
    for row in signals:
        by_day.setdefault(row["day"], []).append(row)

    summaries: list[dict] = []
    opened = 0
    for day_s in trade_days:
        day = date.fromisoformat(day_s)
        set_clock(day_s, "15:15:00")
        marks, rsi, smma, bars = {}, {}, {}, {}
        for position in book.positions:
            symbol = position.symbol
            if not position.is_open or position.entry_time[:10] >= day_s:
                continue
            i = index_of.get(symbol, {}).get(day_s)
            if i is None:
                continue
            price = closes[symbol][day_s]
            marks[symbol] = price
            row = data[symbol][i]
            bars[symbol] = Candle(row[0], row[1], row[2], row[3], row[4])
            frame = frames[symbol]
            fast, slow = frame["fast"], frame["slow"]
            smma[symbol] = (
                None if fast is None or pd.isna(fast.iloc[i]) else float(fast.iloc[i]),
                None if slow is None or pd.isna(slow.iloc[i]) else float(slow.iloc[i]),
            )
            value = frame["rsi"].iloc[i] if frame["rsi"] is not None else None
            if value is not None and not pd.isna(value):
                rsi[symbol] = float(value)
        if marks:
            book.update(marks, day, rsi, smma, candles=bars)

        if not expiry_entry_skip_reason(day):
            for row in by_day.get(day_s, []):
                alert = alert_for(row)
                alert.expiry = futures_expiry(day)
                alert.lot_size = lot_sizes.get(row["symbol"], 0)
                set_clock(day_s, "15:15:00")
                if any(event.kind == "entry" for event in book.open_from_alerts([alert])):
                    opened += 1

        prices = dict(marks)
        for position in book.positions:
            if position.is_open and position.symbol not in prices:
                prices[position.symbol] = closes.get(position.symbol, {}).get(
                    day_s, position.entry_price
                )
        summaries.append(
            book.portfolio_summary_row(
                prices, recorded_at=datetime.strptime(f"{day_s} 15:15:00", "%Y-%m-%d %H:%M:%S")
            )
        )
        if day_s == trade_days[-1] or day_s.endswith("01") or len(summaries) % 10 == 0:
            print(
                f"  {day_s}: open {summaries[-1]['Total number of positions taken']}  "
                f"realised ₹{summaries[-1]['Total realised profit or loss']:+,}  "
                f"total ₹{summaries[-1]['Profit or loss']:+,}"
            )

    last = trade_days[-1]
    final_prices = {}
    for position in book.positions:
        if position.is_open:
            final_prices[position.symbol] = closes.get(position.symbol, {}).get(
                last, position.entry_price
            )
    legs = list(book._pending_rows)
    book.save()
    write_csv(journal_csv, COLUMNS, legs)

    last_row = summaries[-1] if summaries else {}
    print(
        f"\nSeeded {paper.name} {args.start} → {last}\n"
        f"  entries {opened}  closed legs {len(legs)}  "
        f"still open {len([p for p in book.positions if p.is_open])}\n"
        f"  realised ₹{book.realised_pnl:+,.0f}  "
        f"unrealised ₹{book.unrealised(final_prices):+,.0f}  "
        f"total ₹{book.realised_pnl + book.unrealised(final_prices):+,.0f}\n"
        f"  capital used ₹{last_row.get('Capital used in positions', 0):,}  "
        f"free ₹{last_row.get('Capital free', 0):,}\n"
        f"  ledger {ledger.relative_to(ROOT)}\n"
        f"  journal {journal_csv.relative_to(ROOT)}"
    )

    if args.skip_sheets:
        print("  skipped Google Sheets")
        return
    if not paper.google_sheet_id:
        print("  google_sheet_id empty — local files only")
        return

    journal = TradeJournal(
        csv_path=journal_csv,
        sheet_id=paper.google_sheet_id,
        worksheet=paper.google_worksheet,
        summary_worksheet=paper.google_summary_worksheet,
    )
    journal.replace_worksheet(paper.google_worksheet, COLUMNS, legs)
    print(f"  replaced '{paper.google_worksheet}' with {len(legs)} closed lot(s)")
    journal.replace_worksheet(paper.google_summary_worksheet, SUMMARY_COLUMNS, summaries)
    print(
        f"  replaced '{paper.google_summary_worksheet}' with {len(summaries)} "
        "15:15 snapshot(s)"
    )


if __name__ == "__main__":
    main()
