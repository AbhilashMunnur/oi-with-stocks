#!/usr/bin/env python3
"""Five-year, fixed-capital EOD proxy backtest for RSI_CandlePattern.

This is deliberately a daily-bar test, not a claim that historical futures
fills are available.  It tests the *day-2* part of the live RSI-candle rule:
an RSI(14) extreme strong candle, followed by its specified reversal candle.
Signals formed at a daily close are filled at the next session's open, so the
test does not use the signal candle's closing price as an executable fill.

Sizing is fixed, not compounded: each new two-lot position reserves Rs20 lakh
of the Rs4 crore base margin pool (20% margin), i.e. Rs1 crore notional.  P&L
never changes either figure; no more than 20 positions can be open.
"""

from __future__ import annotations

import argparse
import calendar
import json
import time
from dataclasses import dataclass, asdict
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.candle_patterns import Candle, candle_stop_price, reversal_setup
from src.config import CandleConfig, SignalType, load_config
from src.indicators import calculate_rsi_series, calculate_smma, calculate_smma_series


BASE_CAPITAL = 40_000_000.0
MARGIN_PCT = 20.0
MARGIN_PER_TRADE = 2_000_000.0
NOTIONAL_PER_TRADE = MARGIN_PER_TRADE / (MARGIN_PCT / 100)
LOTS_PER_TRADE = 2
MAX_POSITIONS = int(BASE_CAPITAL // MARGIN_PER_TRADE)


@dataclass
class Position:
    symbol: str
    direction: str
    signal_date: str
    entry_date: str
    entry: float
    stop: float
    entry_rsi: float
    pattern: str
    lots_open: int = LOTS_PER_TRADE
    first_exit: float | None = None
    first_exit_date: str | None = None
    first_reason: str | None = None


def last_tuesday(day: pd.Timestamp) -> bool:
    """Match the current configured stock-expiry entry skip rule."""
    if day.weekday() != calendar.TUESDAY:
        return False
    return (day + pd.Timedelta(days=7)).month != day.month


def flatten_yahoo(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if raw.empty:
        return raw
    if isinstance(raw.columns, pd.MultiIndex):
        try:
            raw = raw.xs(ticker, axis=1, level="Ticker", drop_level=True)
        except KeyError:
            raw = raw.droplevel(-1, axis=1)
    return raw.rename(columns=str.title)[["Open", "High", "Low", "Close"]].dropna()


def download(symbols: list[str], start: date, end: date) -> dict[str, pd.DataFrame]:
    """Download in modest batches so Yahoo failures do not lose all symbols."""
    result: dict[str, pd.DataFrame] = {}
    tickers = [f"{symbol}.NS" for symbol in symbols]
    for offset in range(0, len(tickers), 25):
        batch = tickers[offset : offset + 25]
        print(f"Downloading {min(offset + len(batch), len(tickers))}/{len(tickers)}")
        raw = yf.download(
            batch,
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            auto_adjust=False,
            progress=False,
            group_by="column",
            threads=True,
        )
        for ticker in batch:
            symbol = ticker.removesuffix(".NS")
            try:
                frame = flatten_yahoo(raw, ticker)
            except (KeyError, ValueError):
                continue
            if len(frame) >= 80:
                result[symbol] = frame
        time.sleep(0.3)
    return result


def close_leg(position: Position, lots: int, price: float) -> float:
    half_notional = NOTIONAL_PER_TRADE / LOTS_PER_TRADE
    raw_return = (price - position.entry) / position.entry
    pnl = raw_return * half_notional * lots
    return pnl if position.direction == "LONG" else -pnl


def run_symbol_signals(
    symbol: str, bars: pd.DataFrame, cfg: CandleConfig, call: float, put: float,
    no_short_symbols: set[str],
) -> list[dict]:
    """Build day-2 signals, with next-open execution to avoid close look-ahead."""
    rsi = calculate_rsi_series(bars["Close"], period=14)
    signals: list[dict] = []
    index = list(bars.index)
    for i in range(15, len(index) - 1):
        prev, current, next_day = index[i - 1], index[i], index[i + 1]
        prior_bar = Candle(str(prev.date()), *bars.loc[prev, ["Open", "High", "Low", "Close"]])
        reversal_bar = Candle(str(current.date()), *bars.loc[current, ["Open", "High", "Low", "Close"]])
        setup = reversal_setup(
            prior_bar,
            reversal_bar,
            float(rsi.loc[prev]) if pd.notna(rsi.loc[prev]) else None,
            call_threshold=call,
            put_threshold=put,
            cfg=cfg,
        )
        if not setup or last_tuesday(current):
            continue
        signal, pattern = setup
        if signal is SignalType.RSI_CANDLE_SHORT and symbol.upper() in no_short_symbols:
            continue
        entry = float(bars.loc[next_day, "Open"])
        if entry <= 0:
            continue
        signals.append(
            {
                "symbol": symbol,
                "signal_date": current,
                "entry_date": next_day,
                "direction": "SHORT" if signal is SignalType.RSI_CANDLE_SHORT else "LONG",
                "entry": entry,
                "stop": candle_stop_price(signal, reversal=reversal_bar, prior=prior_bar),
                "rsi": float(rsi.loc[prev]),
                "pattern": pattern,
            }
        )
    return signals


def run_backtest(
    data: dict[str, pd.DataFrame], cfg: CandleConfig, call: float, put: float,
    no_short_symbols: set[str],
) -> tuple[list[dict], list[dict], list[dict]]:
    # Indicators are calculated once per symbol.  Recalculating an expanding
    # RSI/SMMA series for every open position and every session is equivalent,
    # but needlessly slow for a five-year, 200-name run.
    for bars in data.values():
        bars["RSI"] = calculate_rsi_series(bars["Close"], period=14)
        bars["SMMA21"] = calculate_smma_series(bars["Close"], period=21)
        bars["SMMA50"] = calculate_smma_series(bars["Close"], period=50)
    signals = [
        row for symbol, bars in data.items()
        for row in run_symbol_signals(symbol, bars, cfg, call, put, no_short_symbols)
    ]
    signal_by_day: dict[pd.Timestamp, list[dict]] = {}
    for signal in signals:
        signal_by_day.setdefault(signal["entry_date"], []).append(signal)

    all_days = sorted({day for bars in data.values() for day in bars.index})
    positions: list[Position] = []
    closed: list[dict] = []
    equity: list[dict] = []
    streaks: dict[str, list[tuple[pd.Timestamp, float]]] = {}
    skips: dict[str, int] = {}

    for day in all_days:
        # Stops use the daily close, matching the live book's cash-close stop.
        # Targets use High/Low; when ambiguity exists, the stop is evaluated first.
        completed_trades: list[tuple[str, float]] = []
        for position in list(positions):
            bars = data[position.symbol]
            if day not in bars.index:
                continue
            bar = bars.loc[day]
            close, high, low = float(bar.Close), float(bar.High), float(bar.Low)
            stop_hit = (position.direction == "LONG" and close <= position.stop) or (
                position.direction == "SHORT" and close >= position.stop
            )
            if stop_hit:
                pnl = close_leg(position, position.lots_open, position.stop)
                closed.append({**asdict(position), "exit_date": str(day.date()), "exit": position.stop, "lots": position.lots_open, "reason": "stop_loss", "pnl": pnl})
                positions.remove(position)
                completed_trades.append((position.symbol, pnl + sum(
                    float(row["pnl"]) for row in closed[:-1]
                    if row["symbol"] == position.symbol and row["entry_date"] == position.entry_date
                )))
                continue

            fast = float(bar.SMMA21) if pd.notna(bar.SMMA21) else None
            slow = float(bar.SMMA50) if pd.notna(bar.SMMA50) else None
            rsi_now = float(bar.RSI) if pd.notna(bar.RSI) else None
            favorable = high if position.direction == "LONG" else low
            is_profit = lambda level: level is not None and ((position.direction == "LONG" and level > position.entry) or (position.direction == "SHORT" and level < position.entry))
            reached = lambda level: level is not None and ((position.direction == "LONG" and favorable >= level) or (position.direction == "SHORT" and favorable <= level))

            if position.lots_open == LOTS_PER_TRADE and is_profit(fast) and reached(fast):
                pnl = close_leg(position, 1, float(fast))
                closed.append({**asdict(position), "exit_date": str(day.date()), "exit": float(fast), "lots": 1, "reason": "first_target", "pnl": pnl})
                position.lots_open = 1
                position.first_exit, position.first_exit_date, position.first_reason = float(fast), str(day.date()), "first_target"

            if position not in positions:
                continue
            rsi_target = rsi_now is not None and ((position.direction == "SHORT" and rsi_now <= 30) or (position.direction == "LONG" and rsi_now >= 70))
            if (is_profit(slow) and reached(slow)) or rsi_target:
                fill = float(slow) if is_profit(slow) and reached(slow) else close
                reason = "second_target" if fill == slow else "rsi_target"
                pnl = close_leg(position, position.lots_open, fill)
                closed.append({**asdict(position), "exit_date": str(day.date()), "exit": fill, "lots": position.lots_open, "reason": reason, "pnl": pnl})
                positions.remove(position)
                completed_trades.append((position.symbol, pnl + sum(
                    float(row["pnl"]) for row in closed[:-1]
                    if row["symbol"] == position.symbol and row["entry_date"] == position.entry_date
                )))

        # Arm / consume the live-book three-loss qualification skip.  The size
        # and available margin remain fixed even after gains or losses.
        for symbol, net in completed_trades:
            streaks.setdefault(symbol, []).append((day, net))
            tail = streaks[symbol][-3:]
            if len(tail) == 3 and all(pnl < 0 for _, pnl in tail) and (tail[-1][0] - tail[0][0]).days <= 14:
                skips[symbol] = 3

        existing = {position.symbol for position in positions}
        for signal in sorted(signal_by_day.get(day, []), key=lambda row: row["symbol"]):
            if len(positions) >= MAX_POSITIONS:
                break
            if signal["symbol"] in existing:
                continue
            remaining_skip = skips.get(signal["symbol"], 0)
            if remaining_skip:
                skips[signal["symbol"]] = remaining_skip - 1
                continue
            # A next-open gap beyond the candle stop means the setup has already
            # failed before a clean daily-bar entry is possible.  Do not pretend
            # it entered at a price that was through its own stop.
            if (signal["direction"] == "LONG" and signal["entry"] <= signal["stop"]) or (
                signal["direction"] == "SHORT" and signal["entry"] >= signal["stop"]
            ):
                continue
            positions.append(Position(
                symbol=signal["symbol"], direction=signal["direction"],
                signal_date=str(signal["signal_date"].date()), entry_date=str(day.date()),
                entry=signal["entry"], stop=signal["stop"], entry_rsi=signal["rsi"], pattern=signal["pattern"],
            ))
            existing.add(signal["symbol"])

        realised = sum(float(row["pnl"]) for row in closed)
        unrealised = 0.0
        for position in positions:
            bars = data[position.symbol]
            if day in bars.index:
                unrealised += close_leg(position, position.lots_open, float(bars.loc[day, "Close"]))
        equity.append({"date": str(day.date()), "realised_pnl": realised, "unrealised_pnl": unrealised, "total_pnl": realised + unrealised, "open_positions": len(positions), "margin_reserved": len(positions) * MARGIN_PER_TRADE})

    # Mark open positions at the last available close; these are explicitly
    # reported separately, so realised P&L is not overstated.
    return closed, equity, [asdict(position) for position in positions]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2021-09-05")
    parser.add_argument("--end", default="2026-09-04")
    parser.add_argument("--out", default="data/backtests/rsi_candle_5y_fixed_4cr")
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    config = load_config(ROOT / "config.yaml")
    symbols = [line.strip() for line in (ROOT / "data" / "nifty200.txt").read_text().splitlines() if line.strip()]
    data = download(symbols, start - timedelta(days=100), end)
    closed, equity, open_positions = run_backtest(
        data, config.candles, config.rsi.call_threshold, config.rsi.put_threshold,
        {symbol.upper() for symbol in config.no_short_symbols},
    )
    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(closed).to_csv(out / "closed_legs.csv", index=False)
    pd.DataFrame(equity).to_csv(out / "equity_curve.csv", index=False)
    curve = pd.DataFrame(equity)
    running_peak = curve["total_pnl"].cummax() if not curve.empty else pd.Series(dtype=float)
    drawdown = curve["total_pnl"] - running_peak if not curve.empty else pd.Series(dtype=float)
    closed_frame = pd.DataFrame(closed)
    pnl_by_exit_year = (
        closed_frame.assign(year=closed_frame["exit_date"].str[:4])
        .groupby("year")["pnl"].sum().round(2).to_dict()
        if not closed_frame.empty else {}
    )
    summary = {
        "period": f"{start} to {end}", "universe": "Current Nifty 200 Yahoo Finance symbols", "symbols_with_data": len(data),
        "base_capital": BASE_CAPITAL, "compounding": False, "margin_per_trade": MARGIN_PER_TRADE,
        "notional_per_trade": NOTIONAL_PER_TRADE, "max_concurrent_positions": MAX_POSITIONS,
        "closed_legs": len(closed),
        "closed_trades": len({(row["symbol"], row["entry_date"]) for row in closed}),
        "closed_trade_pnl": sum(row["pnl"] for row in closed),
        "open_positions": open_positions, "final_mark_to_market_pnl": equity[-1]["total_pnl"] if equity else 0,
        "final_equity": BASE_CAPITAL + (equity[-1]["total_pnl"] if equity else 0),
        "return_on_fixed_base_pct": ((equity[-1]["total_pnl"] if equity else 0) / BASE_CAPITAL * 100),
        "max_pnl_drawdown": float(drawdown.min()) if not drawdown.empty else 0,
        "peak_pnl": max((row["total_pnl"] for row in equity), default=0),
        "pnl_by_exit_year": pnl_by_exit_year,
        "assumptions": [
            "Day-2 reversal setups only; same-day 15:15 setups require intraday archive and are excluded.",
            "Signal at EOD, entry next session open; daily cash OHLC is a futures-price proxy.",
            "Daily close confirms candle stops; target tests use daily high/low after stop precedence.",
            "No brokerage, slippage, taxes, funding, or futures basis. Current Nifty 200 creates survivorship bias.",
        ],
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
