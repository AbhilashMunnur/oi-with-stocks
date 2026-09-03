#!/usr/bin/env python3
"""List last-2-week RSI_CandlePattern hits, split next-day (chart) vs same-day wick."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.candle_patterns import (  # noqa: E402
    reversal_setup,
    same_day_setup,
)
from src.config import load_config  # noqa: E402
from src.data.angelone_client import AngelOneClient  # noqa: E402
from src.data.option_expiry import expiry_entry_skip_reason  # noqa: E402
from src.oi_analyzer import no_short_skip_reason  # noqa: E402

from scripts.rsi_candle_3m_two_week import (  # noqa: E402
    START,
    END,
    ReplayFeed,
    cash_closes_before,
    cash_closes_with_last,
    rsi_on,
    trading_days,
)


def main() -> None:
    config = load_config(ROOT / "config.yaml")
    client = AngelOneClient(
        rsi_period=config.rsi.period,
        history_days=config.data.history_days,
    )
    feed = ReplayFeed(client, config.data.history_days)
    try:
        symbols = client.fno_symbols()
        feed.load_cash_daily(symbols)
        feed.load_cash_intra(symbols)
        days = trading_days(feed, symbols)
        cfg = config.candles
        call_th = config.rsi.call_threshold
        put_th = config.rsi.put_threshold
        period = config.rsi.period
        rows = []
        for day in days:
            if expiry_entry_skip_reason(day):
                continue
            for symbol in symbols:
                yesterday = feed.cash_yesterday(symbol, day)
                today_bar = feed.cash_today(symbol, day)
                if today_bar is None or yesterday is None:
                    continue
                ohlc = feed.cash_daily.get(symbol) or []
                y_rsi = rsi_on(cash_closes_before(ohlc, day), period)
                nxt = reversal_setup(
                    yesterday,
                    today_bar,
                    y_rsi,
                    call_threshold=call_th,
                    put_threshold=put_th,
                    cfg=cfg,
                )
                rsi_close = rsi_on(
                    cash_closes_with_last(ohlc, day, today_bar.close), period
                )
                rsi_high = rsi_on(
                    cash_closes_with_last(ohlc, day, today_bar.high), period
                )
                rsi_low = rsi_on(
                    cash_closes_with_last(ohlc, day, today_bar.low), period
                )
                same = same_day_setup(
                    today_bar,
                    rsi_at_close=rsi_close,
                    rsi_at_high=rsi_high,
                    rsi_at_low=rsi_low,
                    call_threshold=call_th,
                    put_threshold=put_th,
                    cfg=cfg,
                )
                if not nxt and not same:
                    continue
                kind = "next-day" if nxt else "same-day"
                signal, pattern = nxt or same
                close_stretched = (
                    rsi_close is not None
                    and (rsi_close >= call_th or rsi_close <= put_th)
                )
                wick_only = kind == "same-day" and not close_stretched
                blocked = no_short_skip_reason(
                    symbol,
                    config.no_short_symbols,
                    is_short="SHORT" in signal.value,
                )
                rows.append(
                    {
                        "day": day.isoformat(),
                        "symbol": symbol,
                        "kind": kind,
                        "signal": signal.value,
                        "pattern": pattern,
                        "y_rsi": None if y_rsi is None else round(y_rsi, 1),
                        "rsi_close": None if rsi_close is None else round(rsi_close, 1),
                        "rsi_high": None if rsi_high is None else round(rsi_high, 1),
                        "rsi_low": None if rsi_low is None else round(rsi_low, 1),
                        "today_o": round(today_bar.open, 2),
                        "today_h": round(today_bar.high, 2),
                        "today_l": round(today_bar.low, 2),
                        "today_c": round(today_bar.close, 2),
                        "y_o": round(yesterday.open, 2),
                        "y_h": round(yesterday.high, 2),
                        "y_l": round(yesterday.low, 2),
                        "y_c": round(yesterday.close, 2),
                        "wick_only": wick_only,
                        "blocked": blocked,
                    }
                )
        out = ROOT / "data" / "replay" / "rsi_candle_chart_setups.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        nxt = [r for r in rows if r["kind"] == "next-day" and not r["blocked"]]
        same_close = [
            r
            for r in rows
            if r["kind"] == "same-day" and not r["wick_only"] and not r["blocked"]
        ]
        wick = [r for r in rows if r["wick_only"] and not r["blocked"]]
        print(f"next-day chart setups: {len(nxt)}")
        for r in nxt:
            print(
                f"  {r['day']} {r['symbol']:12} {r['signal']:18} {r['pattern']:18} "
                f"yRSI {r['y_rsi']}  cRSI {r['rsi_close']}"
            )
        print(f"\nsame-day, close still 70/30: {len(same_close)}")
        for r in same_close:
            print(
                f"  {r['day']} {r['symbol']:12} {r['signal']:18} {r['pattern']:18} "
                f"cRSI {r['rsi_close']}"
            )
        print(f"\nsame-day wick-only (not a chart 70/30 close): {len(wick)}")
        for r in wick:
            print(
                f"  {r['day']} {r['symbol']:12} {r['signal']:18} {r['pattern']:18} "
                f"cRSI {r['rsi_close']} high {r['rsi_high']} low {r['rsi_low']}"
            )
        print(f"\nWrote {out}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
