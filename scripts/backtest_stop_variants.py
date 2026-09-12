#!/usr/bin/env python3
"""Compare stop-loss rules for RSI_CandlePattern over 1 Aug – 11 Sep 2026.

Replays the same signal set through a fresh book per variant so the only thing
that differs is the stop. Never touches the live ledger.

Variants:
  candle      entry candle's high/low, triggered on the cash close (current live rule)
  pct-N       flat N% from entry on the futures mark
  candle-capN candle stop, but never further than N% from entry
  wider       whichever of the candle stop / N% is further from entry
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import date
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
from src.indicators import calculate_rsi, calculate_smma
from src.oi_analyzer import no_short_skip_reason
from src.paper_trading.book import PaperBook
from src.paper_trading.models import Direction

START, END = date(2026, 8, 1), date(2026, 9, 11)
CACHE = ROOT / "data" / "backtests" / "stop_variants"
SIGNALS_CACHE = CACHE / "signals.json"


def rsi_on(closes, period):
    return calculate_rsi(pd.Series(closes, dtype=float), period=period) if closes else None


def smma_on(closes, period):
    return calculate_smma(pd.Series(closes, dtype=float), period=period) if closes else None


def set_clock(day: date, hhmm: str) -> None:
    book_mod.now_stamp = lambda: f"{day.isoformat()} {hhmm}"


def screen(client, config, symbols) -> list[dict]:
    if SIGNALS_CACHE.exists():
        rows = json.loads(SIGNALS_CACHE.read_text())
        print(f"  reusing {len(rows)} cached signal(s)")
        return rows

    cfg, period = config.candles, config.rsi.period
    call_th, put_th = config.rsi.call_threshold, config.rsi.put_threshold
    low, high = START.isoformat(), END.isoformat()
    found = []
    for index, symbol in enumerate(symbols, 1):
        if index % 50 == 0:
            print(f"  screened {index}/{len(symbols)}")
        try:
            raw = client.daily_full_ohlc(symbol)
        except Exception:
            continue
        bars = [
            Candle(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]))
            for r in raw
            if r[1] and float(r[1]) > 0
        ]
        if len(bars) < 20:
            continue
        closes = [b.close for b in bars]
        for i, today in enumerate(bars):
            if i < 1 or not (low <= today.date <= high):
                continue
            prior = closes[:i]
            y_rsi = rsi_on(prior, period)
            setup = reversal_setup(
                bars[i - 1], today, y_rsi,
                call_threshold=call_th, put_threshold=put_th, cfg=cfg,
            )
            same_day, rsi_val = False, y_rsi
            if setup:
                stop = candle_stop_price(
                    setup[0], reversal=today, prior=bars[i - 1], same_day=False
                )
            else:
                rc = rsi_on(prior + [today.close], period)
                rh = rsi_on(prior + [today.high], period)
                rl = rsi_on(prior + [today.low], period)
                setup = same_day_setup(
                    today, rsi_at_close=rc, rsi_at_high=rh, rsi_at_low=rl,
                    call_threshold=call_th, put_threshold=put_th, cfg=cfg,
                )
                if not setup:
                    continue
                same_day, rsi_val = True, (rc or rh or rl)
                stop = candle_stop_price(setup[0], reversal=today, prior=None, same_day=True)
            signal, pattern = setup
            if no_short_skip_reason(
                symbol, config.no_short_symbols,
                is_short=signal is SignalType.RSI_CANDLE_SHORT,
            ):
                continue
            found.append({
                "day": today.date, "symbol": symbol, "signal": signal.value,
                "pattern": pattern, "stop": stop, "rsi": round(float(rsi_val or 0), 2),
                "kind": "same-day" if same_day else "next-day",
            })
    found.sort(key=lambda r: (r["day"], r["symbol"]))
    CACHE.mkdir(parents=True, exist_ok=True)
    SIGNALS_CACHE.write_text(json.dumps(found, indent=2))
    print(f"  cached {len(found)} signal(s)")
    return found


def run(name: str, paper, signals, feed, config, *, use_candle_stop: bool, cap_pct=None,
        sides=("SHORT", "LONG")) -> dict:
    """Replay every signal through a fresh book under one stop rule."""
    ledger = CACHE / f"book_{name}.json"
    if ledger.exists():
        ledger.unlink()
    book = PaperBook(paper, path=ledger, journal=None,
                     no_short_symbols=config.no_short_symbols)

    by_day = {}
    for row in signals:
        by_day.setdefault(row["day"], []).append(row)
    held_contracts = {}
    opened = 0

    for day_s in feed["sessions"]:
        day = date.fromisoformat(day_s)

        # exits on everything opened earlier
        set_clock(day, "15:15:00")
        marks, cash_stops, smma, rsi = {}, {}, {}, {}
        for position in book.positions:
            if not position.is_open or position.entry_time[:10] >= day_s:
                continue
            key = held_contracts.get(position.symbol)
            prices = feed["fut"].get(key) or []
            prior = [c for s, c in prices if s <= day_s]
            if not prior:
                continue
            mark = prior[-1]
            marks[position.symbol] = mark
            same = [c for s, c in feed["cash"][position.symbol] if s == day_s]
            if same:
                cash_stops[position.symbol] = same[0]
            history = [c for s, c in feed["cash"][position.symbol] if s < day_s] + [mark]
            smma[position.symbol] = (
                smma_on(history, paper.smma_fast), smma_on(history, paper.smma_slow)
            )
            value = rsi_on(history, config.rsi.period)
            if value is not None:
                rsi[position.symbol] = value
        if marks:
            book.update(marks, day, rsi, smma,
                        stop_prices=cash_stops if use_candle_stop else None)

        # entries
        if expiry_entry_skip_reason(day):
            continue
        for row in by_day.get(day_s, []):
            symbol = row["symbol"]
            signal = SignalType(row["signal"])
            side = "SHORT" if signal is SignalType.RSI_CANDLE_SHORT else "LONG"
            if side not in sides:
                continue
            contract = feed["contract"].get((symbol, day_s))
            if not contract:
                continue
            key = f"{contract.exchange}:{contract.token}"
            fills = [c for s, c in (feed["fut"].get(key) or []) if s <= day_s]
            if not fills:
                continue
            entry = fills[-1]

            stop = row["stop"]
            if not use_candle_stop:
                stop = None                      # fall back to the percent stop
            elif cap_pct is not None and stop:
                # Never let the candle stop sit further than cap_pct from entry.
                limit = (
                    entry * (1 + cap_pct / 100) if side == "SHORT"
                    else entry * (1 - cap_pct / 100)
                )
                stop = min(stop, limit) if side == "SHORT" else max(stop, limit)

            alert = make_candle_alert(
                symbol=symbol, ltp=entry, rsi=row["rsi"], signal=signal,
                pattern=row["pattern"], stop_price=stop,
            )
            alert.expiry = contract.expiry
            alert.lot_size = contract.lot_size
            set_clock(day, "15:15:00")
            events = book.open_from_alerts([alert])
            if any(e.kind == "entry" for e in events):
                held_contracts[symbol] = key
                opened += 1

    # mark whatever is still open at the last session
    last = feed["sessions"][-1]
    final = {}
    for position in book.positions:
        if not position.is_open:
            continue
        prices = feed["fut"].get(held_contracts.get(position.symbol)) or []
        prior = [c for s, c in prices if s <= last]
        final[position.symbol] = prior[-1] if prior else position.entry_price

    legs = book._pending_rows
    stops = [r for r in legs if r["Exit reason"] == "stop_loss"]
    wins = [r for r in legs if float(r["Profit/loss"]) > 0]
    unreal = book.unrealised(final)
    return {
        "variant": name,
        "opened": opened,
        "legs": len(legs),
        "stops": len(stops),
        "win_legs": len(wins),
        "realised": round(book.realised_pnl),
        "unrealised": round(unreal),
        "total": round(book.realised_pnl + unreal),
        "still_open": len([p for p in book.positions if p.is_open]),
        "max_margin": round(book.margin_blocked),
    }


def main() -> None:
    config = load_config(ROOT / "config.yaml")
    base = config.rsi_candle_2w_paper_trading
    client = AngelOneClient(
        rsi_period=config.rsi.period,
        history_days=config.data.history_days,
        extreme_history_days=config.data.extreme_history_days,
    )
    try:
        symbols = client.fno_symbols()
        print(f"Screening {len(symbols)} names {START} → {END}…")
        signals = screen(client, config, symbols)

        print("\nLoading prices for the signal names…")
        feed = {"fut": {}, "cash": {}, "contract": {}, "sessions": []}
        names = sorted({r["symbol"] for r in signals})
        for i, row in enumerate(signals, 1):
            symbol, day_s = row["symbol"], row["day"]
            contract = client.futures_contract(
                symbol, month_index=base.futures_month, as_of=date.fromisoformat(day_s)
            )
            if not contract or not contract.token:
                continue
            feed["contract"][(symbol, day_s)] = contract
            key = f"{contract.exchange}:{contract.token}"
            if key not in feed["fut"]:
                feed["fut"][key] = client._fetch_futures_daily(contract)
            if i % 100 == 0:
                print(f"  {i}/{len(signals)} signals priced")
        for symbol in names:
            feed["cash"][symbol] = [
                (str(r[0])[:10], float(r[4]))
                for r in client.daily_full_ohlc(symbol)
                if r[4] and float(r[4]) > 0
            ]
        feed["sessions"] = sorted({
            s for symbol in names[:40] for s, _ in feed["cash"][symbol]
            if START.isoformat() <= s <= END.isoformat()
        })
        print(f"  {len(feed['sessions'])} sessions, {len(names)} names")
    finally:
        client.close()

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
        ("pct-6", dict(use_candle_stop=False),
         replace(base, stop_loss_pct=6.0, second_lot_stop_pct=6.0)),
        ("candle capped 2%", dict(use_candle_stop=True, cap_pct=2.0), base),
        ("pct-2, shorts only", dict(use_candle_stop=False, sides=("SHORT",)),
         replace(base, stop_loss_pct=2.0, second_lot_stop_pct=2.0)),
        ("pct-3, shorts only", dict(use_candle_stop=False, sides=("SHORT",)),
         replace(base, stop_loss_pct=3.0, second_lot_stop_pct=3.0)),
        ("pct-4, shorts only", dict(use_candle_stop=False, sides=("SHORT",)),
         replace(base, stop_loss_pct=4.0, second_lot_stop_pct=4.0)),
        ("candle, shorts only", dict(use_candle_stop=True, sides=("SHORT",)), base),
    ]

    results = []
    for name, kwargs, paper in variants:
        print(f"\n--- {name} ---")
        out = run(name, paper, signals, feed, config, **kwargs)
        results.append(out)
        print(f"  opened {out['opened']}  legs {out['legs']}  stops {out['stops']}  "
              f"realised {out['realised']:,}  unreal {out['unrealised']:,}  total {out['total']:,}")

    print("\n" + "=" * 104)
    print(f"{'variant':22} {'opened':>7} {'legs':>6} {'stop%':>7} {'leg win%':>9} "
          f"{'realised':>12} {'unreal':>12} {'TOTAL':>12}")
    print("-" * 104)
    for r in results:
        stop_share = r["stops"] / r["legs"] if r["legs"] else 0
        win = r["win_legs"] / r["legs"] if r["legs"] else 0
        print(f"{r['variant']:22} {r['opened']:>7} {r['legs']:>6} {stop_share:>6.0%} "
              f"{win:>8.0%} {r['realised']:>12,} {r['unrealised']:>12,} {r['total']:>12,}")

    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / "results.json").write_text(json.dumps(results, indent=2))
    print(f"\nWrote {CACHE / 'results.json'}")


if __name__ == "__main__":
    main()
