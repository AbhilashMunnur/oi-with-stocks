#!/usr/bin/env python3
"""One-year stop-loss backtest for RSI_CandlePattern on the F&O universe.

Daily bars from Angel One (not Yahoo). Both entry modes of the live rule are
tested — next-day after an RSI 70/30 tag, and same-day when the bar has already
reversed. Exits run through the real `PaperBook`, so targets, the SMMA race,
margin, and the loss-streak skip behave exactly as they do in production.

Fills are the signal day's daily close, standing in for the live 15:15 entry,
and cash prices stand in for stock futures. Percentage outcomes are what this
measures; absolute rupees are indicative.

    .venv/bin/python scripts/backtest_rsi_candle_1y.py --days 450
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
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
from src.data.option_expiry import expiry_entry_skip_reason
from src.indicators import calculate_rsi_series, calculate_smma_series
from src.paper_trading.journal import DATE_FORMAT
from src.oi_analyzer import no_short_skip_reason
from src.paper_trading.book import PaperBook

CACHE = ROOT / "data" / "backtests" / "rsi_candle_1y"
PERIOD = 14


def set_clock(day: str, hhmm: str) -> None:
    book_mod.now_stamp = lambda: f"{day} {hhmm}"


def wilder(closes: pd.Series, period: int = PERIOD):
    delta = closes.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    avg_gain = gains.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = losses.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    return avg_gain, avg_loss


def rsi_at(avg_gain: float, avg_loss: float, prev_close: float, price: float,
           period: int = PERIOD) -> float | None:
    """RSI if `price` were today's close, given yesterday's Wilder state."""
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


def leg_charges(row: dict, margin_pct: float) -> float:
    """Round-trip cost of one leg under the current NSE stock-futures schedule."""
    entry = float(row["Entry price"])
    exit_ = float(row["Exit price"])
    if entry <= 0:
        return 0.0
    # "Capital needed" is written for humans as "<margin per lot>*<lots>".
    per_lot, _, lots = str(row["Capital needed"]).partition("*")
    margin = float(per_lot) * (float(lots) if lots else 1.0)
    entry_notional = margin / (margin_pct / 100)
    exit_notional = entry_notional * (exit_ / entry)
    if row["Buy/Sell"] == "Sell":
        sell, buy = entry_notional, exit_notional
    else:
        buy, sell = entry_notional, exit_notional
    turnover = buy + sell

    brokerage = 40.0                       # Rs 20 per order, two orders
    stt = sell * 0.0002
    stamp = buy * 0.00002
    exchange = turnover * 0.0000188
    sebi = turnover * 10 / 10_000_000
    clearing = turnover * 0.000005
    gst = (brokerage + exchange + sebi + clearing) * 0.18
    return brokerage + stt + stamp + exchange + sebi + clearing + gst


def load_bars(client: AngelOneClient, symbols: list[str], days: int) -> dict[str, list]:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"ohlc_{days}d.json"
    if path.exists():
        data = json.loads(path.read_text())
        print(f"  reusing cached bars for {len(data)} name(s)")
        return data

    data: dict[str, list] = {}
    for index, symbol in enumerate(symbols, 1):
        rows = client._request_candles(symbol, days=days)
        kept = [
            [str(r[0])[:10], float(r[1]), float(r[2]), float(r[3]), float(r[4])]
            for r in rows or []
            if len(r) >= 5 and r[1] and float(r[1]) > 0
        ]
        if len(kept) > 80:
            data[symbol] = kept
        if index % 25 == 0:
            print(f"  fetched {index}/{len(symbols)}")
            path.write_text(json.dumps(data))
    path.write_text(json.dumps(data))
    print(f"  cached {len(data)} name(s) -> {path.name}")
    return data


def build_signals(data: dict[str, list], config, lot_sizes: dict[str, int]) -> list[dict]:
    cfg = config.candles
    call_th, put_th = config.rsi.call_threshold, config.rsi.put_threshold
    out: list[dict] = []

    for symbol, rows in data.items():
        closes = pd.Series([r[4] for r in rows], dtype=float)
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
                yesterday, today, y_rsi,
                call_threshold=call_th, put_threshold=put_th, cfg=cfg,
            )
            kind, rsi_val = "next-day", y_rsi
            if setup:
                stop = candle_stop_price(setup[0], reversal=today, prior=yesterday,
                                         same_day=False)
            else:
                state = (avg_gain.iloc[i - 1], avg_loss.iloc[i - 1], prev[4])
                rc = rsi_at(*state, today.close)
                rh = rsi_at(*state, today.high)
                rl = rsi_at(*state, today.low)
                setup = same_day_setup(
                    today, rsi_at_close=rc, rsi_at_high=rh, rsi_at_low=rl,
                    call_threshold=call_th, put_threshold=put_th, cfg=cfg,
                )
                if not setup:
                    continue
                kind, rsi_val = "same-day", (rc or rh or rl)
                stop = candle_stop_price(setup[0], reversal=today, prior=None,
                                         same_day=True)

            signal, pattern = setup
            if no_short_skip_reason(
                symbol, config.no_short_symbols,
                is_short=signal is SignalType.RSI_CANDLE_SHORT,
            ):
                continue
            if lot_sizes.get(symbol, 0) <= 0:
                continue
            out.append({
                "day": cur[0], "symbol": symbol, "signal": signal.value,
                "pattern": pattern, "stop": stop, "entry": cur[4],
                "rsi": round(float(rsi_val or 0), 2), "kind": kind,
            })
    out.sort(key=lambda r: (r["day"], r["symbol"]))
    return out


def run(name: str, paper, signals, data, lot_sizes, config, sessions, *,
        use_candle_stop: bool, sides=("SHORT", "LONG")) -> dict:
    ledger = CACHE / f"book_{name.replace(' ', '_').replace('%', 'pc')}.json"
    if ledger.exists():
        ledger.unlink()
    book = PaperBook(paper, path=ledger, journal=None,
                     no_short_symbols=config.no_short_symbols,
                     candle_cfg=config.candles)

    closes = {s: {r[0]: r[4] for r in rows} for s, rows in data.items()}
    frames = {}
    for symbol, rows in data.items():
        series = pd.Series([r[4] for r in rows], dtype=float)
        frames[symbol] = {
            "dates": [r[0] for r in rows],
            "rsi": calculate_rsi_series(series, PERIOD),
            "f": calculate_smma_series(series, paper.smma_fast),
            "s": calculate_smma_series(series, paper.smma_slow),
        }
    index_of = {s: {d: i for i, d in enumerate(f["dates"])} for s, f in frames.items()}

    by_day: dict[str, list[dict]] = {}
    for row in signals:
        by_day.setdefault(row["day"], []).append(row)

    opened = 0
    peak_margin = 0.0
    for day_s in sessions:
        set_clock(day_s, "15:30:00")
        marks, stops, smma, rsi, bars = {}, {}, {}, {}, {}
        for position in book.positions:
            symbol = position.symbol
            if not position.is_open or position.entry_time[:10] >= day_s:
                continue
            i = index_of[symbol].get(day_s)
            if i is None:
                continue
            price = closes[symbol][day_s]
            marks[symbol] = price
            stops[symbol] = price
            row = data[symbol][i]
            bars[symbol] = Candle(row[0], row[1], row[2], row[3], row[4])
            frame = frames[symbol]
            fast, slow = frame["f"], frame["s"]
            smma[symbol] = (
                None if fast is None or pd.isna(fast.iloc[i]) else float(fast.iloc[i]),
                None if slow is None or pd.isna(slow.iloc[i]) else float(slow.iloc[i]),
            )
            value = frame["rsi"].iloc[i]
            if not pd.isna(value):
                rsi[symbol] = float(value)
        if marks:
            book.update(marks, date.fromisoformat(day_s), rsi, smma,
                        stop_prices=stops if use_candle_stop else None,
                        candles=bars)

        if expiry_entry_skip_reason(date.fromisoformat(day_s)):
            continue
        for row in by_day.get(day_s, []):
            signal = SignalType(row["signal"])
            side = "SHORT" if signal is SignalType.RSI_CANDLE_SHORT else "LONG"
            if side not in sides:
                continue
            alert = make_candle_alert(
                symbol=row["symbol"], ltp=row["entry"], rsi=row["rsi"], signal=signal,
                pattern=row["pattern"],
                stop_price=row["stop"] if use_candle_stop else None,
            )
            alert.expiry = ""
            alert.lot_size = lot_sizes.get(row["symbol"], 0)
            set_clock(day_s, "15:15:00")
            if any(e.kind == "entry" for e in book.open_from_alerts([alert])):
                opened += 1
        peak_margin = max(peak_margin, book.margin_blocked)

    last = sessions[-1]
    final = {}
    for position in book.positions:
        if position.is_open:
            final[position.symbol] = closes[position.symbol].get(
                last, position.entry_price
            )
    legs = book._pending_rows
    wins = [r for r in legs if float(r["Profit/loss"]) > 0]
    stopped = [r for r in legs if r["Exit reason"] == "stop_loss"]
    unreal = book.unrealised(final)

    monthly: dict[str, float] = {}
    for row in legs:
        month = datetime.strptime(str(row["Exit date"]), DATE_FORMAT).strftime("%Y-%m")
        monthly[month] = monthly.get(month, 0.0) + float(row["Profit/loss"])
    running, peak, trough = 0.0, 0.0, 0.0
    for month in sorted(monthly):
        running += monthly[month]
        peak = max(peak, running)
        trough = min(trough, running - peak)
    charges = sum(leg_charges(row, paper.margin_pct) for row in legs)
    return {
        "monthly": {m: round(v) for m, v in sorted(monthly.items())},
        "max_drawdown": round(trough),
        "charges": round(charges),
        "net_total": round(book.realised_pnl + unreal - charges),
        "capital": paper.capital,
        "lots": paper.lots_per_trade,
        "by_reason": {
            reason: [
                sum(1 for r in legs if r["Exit reason"] == reason),
                round(sum(float(r["Profit/loss"]) for r in legs
                          if r["Exit reason"] == reason)),
            ]
            for reason in sorted({r["Exit reason"] for r in legs})
        },
        "variant": name, "opened": opened, "legs": len(legs),
        "stops": len(stopped), "win_legs": len(wins),
        "realised": round(book.realised_pnl), "unrealised": round(unreal),
        "total": round(book.realised_pnl + unreal),
        "still_open": len([p for p in book.positions if p.is_open]),
        "peak_margin": round(peak_margin),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=450)
    parser.add_argument("--start", default="", help="first trading day, e.g. 2026-05-01")
    parser.add_argument("--ladder", action="store_true",
                        help="compare the 3-lot / Rs 4 Cr ladder instead of stop levels")
    args = parser.parse_args()

    config = load_config(ROOT / "config.yaml")
    # Pin every structural field rather than inheriting it, so a variant named
    # "2 lot 2Cr" stays that whatever config.yaml happens to say today.
    base = replace(
        config.rsi_candle_2w_paper_trading,
        capital=20_000_000, lots_per_trade=2,
        stop_loss_pct=4.0, second_lot_stop_pct=1.0,
        final_lot_smma_cross_exit=False, final_lot_no_stop=False,
    )
    client = AngelOneClient(
        rsi_period=config.rsi.period,
        history_days=config.data.history_days,
        extreme_history_days=config.data.extreme_history_days,
    )
    try:
        symbols = client.fno_symbols()
        print(f"Loading {args.days}d of daily bars for {len(symbols)} F&O names…")
        data = load_bars(client, symbols, args.days)
        lot_sizes = {s: client.lot_size(s) or 0 for s in data}
    finally:
        client.close()

    sessions = sorted({r[0] for rows in data.values() for r in rows})
    warm = sessions[PERIOD + 40:]          # let RSI and SMMA 50 settle
    if args.start:
        warm = [d for d in warm if d >= args.start]
    print(f"  {len(data)} names, {len(sessions)} sessions "
          f"({sessions[0]} → {sessions[-1]}); trading from {warm[0]}")

    signals = build_signals(data, config, lot_sizes)
    signals = [s for s in signals if s["day"] >= warm[0]]
    print(f"  {len(signals)} signal(s) in window")

    # The proposed book: Rs 4 Cr, 3 lots, and a runner that rides for RSI 30/70
    # or a strong close back through SMMA 21, carrying no stop of its own.
    ladder = replace(
        base, capital=40_000_000, lots_per_trade=3,
        stop_loss_pct=2.0, second_lot_stop_pct=2.0,
        final_lot_smma_cross_exit=True, final_lot_no_stop=True,
    )
    if args.ladder:
        variants = [
            ("2 lot 2Cr pct-2", dict(use_candle_stop=False),
             replace(base, stop_loss_pct=2.0, second_lot_stop_pct=2.0)),
            ("2 lot 4Cr pct-2", dict(use_candle_stop=False),
             replace(base, capital=40_000_000, stop_loss_pct=2.0,
                     second_lot_stop_pct=2.0)),
            ("3 lot 4Cr ladder", dict(use_candle_stop=False), ladder),
            ("3 lot, stop on runner", dict(use_candle_stop=False),
             replace(ladder, final_lot_no_stop=False)),
            ("3 lot, no SMMA exit", dict(use_candle_stop=False),
             replace(ladder, final_lot_smma_cross_exit=False)),
            ("3 lot ladder, shorts", dict(use_candle_stop=False, sides=("SHORT",)),
             ladder),
            ("3 lot ladder, longs", dict(use_candle_stop=False, sides=("LONG",)),
             ladder),
            ("3 lot 4Cr candle stop", dict(use_candle_stop=True),
             replace(ladder, candle_stop=True)),
        ]
    else:
        variants = [
        ("candle (live)", dict(use_candle_stop=True), base),
        ("pct-2", dict(use_candle_stop=False),
         replace(base, stop_loss_pct=2.0, second_lot_stop_pct=2.0)),
        ("pct-3", dict(use_candle_stop=False),
         replace(base, stop_loss_pct=3.0, second_lot_stop_pct=3.0)),
        ("pct-4", dict(use_candle_stop=False),
         replace(base, stop_loss_pct=4.0, second_lot_stop_pct=4.0)),
        ("pct-5", dict(use_candle_stop=False),
         replace(base, stop_loss_pct=5.0, second_lot_stop_pct=5.0)),
        ("pct-3 shorts", dict(use_candle_stop=False, sides=("SHORT",)),
         replace(base, stop_loss_pct=3.0, second_lot_stop_pct=3.0)),
        ("pct-3 longs", dict(use_candle_stop=False, sides=("LONG",)),
         replace(base, stop_loss_pct=3.0, second_lot_stop_pct=3.0)),
        ("candle shorts", dict(use_candle_stop=True, sides=("SHORT",)), base),
    ]

    results = []
    for name, kwargs, paper in variants:
        out = run(name, paper, signals, data, lot_sizes, config, warm, **kwargs)
        results.append(out)
        print(f"  {name:16} opened {out['opened']:>4}  legs {out['legs']:>4}  "
              f"realised {out['realised']:>12,}  total {out['total']:>12,}")

    print("\n" + "=" * 112)
    print(f"RSI_CandlePattern · {warm[0]} → {sessions[-1]} · {len(data)} F&O names · "
          f"20% margin · ret% is on each variant's own capital")
    print("=" * 112)
    print(f"{'variant':22} {'cap':>5} {'lots':>5} {'trades':>7} {'stop%':>6} "
          f"{'win%':>6} {'realised':>13} {'unrealised':>12} {'charges':>10} "
          f"{'NET':>13} {'ret%':>7}")
    print("-" * 112)
    for r in results:
        share = r["stops"] / r["legs"] if r["legs"] else 0
        win = r["win_legs"] / r["legs"] if r["legs"] else 0
        print(f"{r['variant']:22} {r['capital'] / 10_000_000:>4.0f}Cr {r['lots']:>5} "
              f"{r['opened']:>7} {share:>5.0%} {win:>5.0%} {r['realised']:>13,} "
              f"{r['unrealised']:>12,} {-r['charges']:>10,} {r['net_total']:>13,} "
              f"{r['net_total'] / r['capital']:>6.1%}")

    CACHE.mkdir(parents=True, exist_ok=True)
    months = sorted({m for r in results for m in r["monthly"]})
    print("\nRealised P&L by exit month")
    print("-" * 112)
    print(f"{'variant':22}" + "".join(f"{m[2:]:>11}" for m in months))
    for r in results:
        cells = "".join(f"{r['monthly'].get(m, 0) / 100000:>10.1f}L" for m in months)
        print(f"{r['variant']:22}{cells}")
    reasons = sorted({k for r in results for k in r["by_reason"]})
    print("\nLegs closed by each exit rule (count / realised P&L)")
    print("-" * 112)
    print(f"{'variant':22}" + "".join(f"{k[:13]:>18}" for k in reasons))
    for r in results:
        cells = ""
        for k in reasons:
            count, pnl = r["by_reason"].get(k, (0, 0))
            cells += f"{f'{count} / {pnl / 100000:.1f}L':>18}"
        print(f"{r['variant']:22}{cells}")

    print("\nWorst peak-to-trough on realised P&L (month end):")
    for r in results:
        print(f"  {r['variant']:22} {r['max_drawdown']:>13,}  "
              f"peak margin used {r['peak_margin']:>13,}")

    name = f"results{'_ladder' if args.ladder else ''}" \
           f"{'_' + args.start if args.start else ''}.json"
    (CACHE / name).write_text(json.dumps(
        {"window": [warm[0], sessions[-1]], "names": len(data),
         "signals": len(signals), "results": results}, indent=2))
    print(f"\nWrote {CACHE / name}")


if __name__ == "__main__":
    main()
