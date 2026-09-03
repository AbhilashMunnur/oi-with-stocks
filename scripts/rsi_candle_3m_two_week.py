#!/usr/bin/env python3
"""Replay RSI_CandlePattern on 3rd-month stock futures, last ~2 weeks, 15:15 IST.

Signals still use the live candle/RSI rules (cash session bar as of 15:15, with
the 3rd-month futures print overlaid on today's close like the scanner). Fills,
SMMA marks, and P&L use 3rd-month futures. Candle stops fire only when the
*cash* close is through the cash-bar stop — a futures print through that level
is not enough. Does not touch the live 2nd-month ledger.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from time import sleep, time

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
    waiting_reason,
    with_live_close,
)
from src.config import SignalType, load_config
from src.data.angelone_client import AngelOneClient, StockFuture
from src.data.base import CACHE_DIR
from src.data.option_expiry import expiry_entry_skip_reason
from src.indicators import calculate_rsi, calculate_smma
from src.oi_analyzer import ScanAlert, no_short_skip_reason
from src.paper_trading.book import PaperBook
from src.paper_trading.models import Direction, ExitReason

START = date(2026, 8, 19)
END = date(2026, 9, 2)
FUTURES_MONTH = 3
ENTRY_STAMP = "15:15:00"
EXIT_STAMP = "15:30:00"
REPLAY_DIR = ROOT / "data" / "replay"
LEDGER = ROOT / "data" / "rsi_candle_3m_2w_paper_book.json"
JOURNAL = ROOT / "data" / "rsi_candle_3m_2w_paper_trades.csv"
RESULT = REPLAY_DIR / "rsi_candle_3m_2w_replay.json"
CHART_ONLY = "--chart-only" in sys.argv


def set_clock(day: date, hhmm: str) -> None:
    stamp = f"{day.isoformat()} {hhmm}"
    book_mod.now_stamp = lambda: stamp


def parse_ts(raw: str) -> datetime | None:
    text = str(raw).replace("T", " ")
    try:
        return datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def nfo_candles(
    client: AngelOneClient,
    token: str,
    interval: str,
    fromdate: str,
    todate: str,
) -> list:
    CACHE_DIR.mkdir(exist_ok=True)
    safe_from = fromdate[:10]
    safe_to = todate[:10]
    path = CACHE_DIR / f"nfo_{interval}_{token}_{safe_from}_{safe_to}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    try:
        response = client._call(
            client._candle_throttle,
            "getCandleData",
            {
                "exchange": "NFO",
                "symboltoken": token,
                "interval": interval,
                "fromdate": fromdate,
                "todate": todate,
            },
        )
    except Exception as exc:
        print(f"  NFO {token} {interval} failed ({exc})")
        return []
    rows = (response or {}).get("data") or []
    if rows:
        path.write_text(json.dumps(rows), encoding="utf-8")
    sleep(1.6)
    return rows


def parse_ohlc_intra(
    rows: list,
) -> list[tuple[datetime, float, float, float, float]]:
    out: list[tuple[datetime, float, float, float, float]] = []
    for row in rows:
        if len(row) < 5:
            continue
        ts = parse_ts(row[0])
        if ts is None:
            continue
        out.append((ts, float(row[1]), float(row[2]), float(row[3]), float(row[4])))
    return out


def session_bar(
    intra: list[tuple[datetime, float, float, float, float]],
    day: date,
    until_hm: tuple[int, int],
    after_hm: tuple[int, int] | None = None,
) -> Candle | None:
    """Aggregate 15-min bars with start in [after_hm, until_hm).

    15:15 snapshot = until (15, 15) so the 15:00 bar (15:00–15:15) is included.
    """
    kept = []
    for bar in intra:
        if bar[0].date() != day:
            continue
        hm = (bar[0].hour, bar[0].minute)
        if hm >= until_hm:
            continue
        if after_hm is not None and hm < after_hm:
            continue
        kept.append(bar)
    if not kept:
        return None
    return Candle(
        date=day.isoformat(),
        open=kept[0][1],
        high=max(bar[2] for bar in kept),
        low=min(bar[3] for bar in kept),
        close=kept[-1][4],
    )


def rsi_on(closes: list[float], period: int) -> float | None:
    if not closes:
        return None
    return calculate_rsi(pd.Series(closes, dtype=float), period=period)


def smma_on(closes: list[float], period: int) -> float | None:
    if not closes:
        return None
    return calculate_smma(pd.Series(closes, dtype=float), period=period)


def cash_closes_before(
    ohlc: list[tuple[str, float, float, float, float]], day: date
) -> list[float]:
    key = day.isoformat()
    return [close for date_s, _o, _h, _l, close in ohlc if date_s < key]


def cash_closes_with_last(
    ohlc: list[tuple[str, float, float, float, float]],
    day: date,
    last: float,
) -> list[float]:
    closes = cash_closes_before(ohlc, day)
    closes.append(last)
    return closes


@dataclass
class ContractData:
    contract: StockFuture
    intra: list[tuple[datetime, float, float, float, float]]


class ReplayFeed:
    def __init__(self, client: AngelOneClient, history_days: int):
        self.client = client
        self.history_days = history_days
        self.cash_daily: dict[str, list[tuple[str, float, float, float, float]]] = {}
        self.cash_intra: dict[str, list[tuple[datetime, float, float, float, float]]] = {}
        self.fut: dict[str, ContractData] = {}
        self.intra_from = f"{(START - timedelta(days=1)).isoformat()} 09:15"
        self.intra_to = f"{END.isoformat()} 15:30"

    def load_cash_daily(self, symbols: list[str]) -> None:
        print(f"Cash daily OHLC for {len(symbols)} names...")
        for index, symbol in enumerate(symbols, 1):
            try:
                self.cash_daily[symbol] = self.client.daily_full_ohlc(symbol)
            except Exception as exc:
                print(f"  {symbol}: cash daily failed ({exc})")
                self.cash_daily[symbol] = []
            if index % 25 == 0:
                print(f"  cash daily {index}/{len(symbols)}")
                self.client._save_ohlc_cache()
                self.client._save_closes_cache()
        self.client._save_ohlc_cache()
        self.client._save_closes_cache()

    def load_cash_intra(self, symbols: list[str]) -> None:
        print(f"Cash 15-min {START}–{END} for {len(symbols)} names...")
        self.client._load_instruments()
        tokens = self.client._equity_tokens or {}
        for index, symbol in enumerate(symbols, 1):
            token = tokens.get(symbol)
            if not token:
                self.cash_intra[symbol] = []
                continue
            path = CACHE_DIR / (
                f"eq_FIFTEEN_MINUTE_{token}_{START.isoformat()}_{END.isoformat()}.json"
            )
            if path.exists():
                rows = json.loads(path.read_text(encoding="utf-8"))
            else:
                try:
                    response = self.client._call(
                        self.client._candle_throttle,
                        "getCandleData",
                        {
                            "exchange": "NSE",
                            "symboltoken": token,
                            "interval": "FIFTEEN_MINUTE",
                            "fromdate": self.intra_from,
                            "todate": self.intra_to,
                        },
                    )
                except Exception as exc:
                    print(f"  {symbol}: cash 15-min failed ({exc})")
                    rows = []
                else:
                    rows = (response or {}).get("data") or []
                    CACHE_DIR.mkdir(exist_ok=True)
                    if rows:
                        path.write_text(json.dumps(rows), encoding="utf-8")
                    sleep(1.6)
            self.cash_intra[symbol] = parse_ohlc_intra(rows)
            if index % 25 == 0:
                print(f"  cash 15-min {index}/{len(symbols)}")

    def contract_data(self, symbol: str, as_of: date) -> ContractData | None:
        contract = self.client.futures_contract(
            symbol, month_index=FUTURES_MONTH, as_of=as_of
        )
        if not contract or not contract.token:
            return None
        cached = self.fut.get(contract.token)
        if cached:
            return cached
        intra = parse_ohlc_intra(
            nfo_candles(
                self.client,
                contract.token,
                "FIFTEEN_MINUTE",
                self.intra_from,
                self.intra_to,
            )
        )
        data = ContractData(contract=contract, intra=intra)
        self.fut[contract.token] = data
        return data

    def fut_bar(
        self,
        symbol: str,
        as_of: date,
        until: tuple[int, int],
        *,
        session_day: date | None = None,
        after: tuple[int, int] | None = None,
    ) -> Candle | None:
        data = self.contract_data(symbol, as_of)
        if not data:
            return None
        day = session_day or as_of
        bar = session_bar(data.intra, day, until, after_hm=after)
        if bar:
            return bar
        return None

    def cash_bar(
        self,
        symbol: str,
        day: date,
        until: tuple[int, int],
        after: tuple[int, int] | None = None,
    ) -> Candle | None:
        return session_bar(
            self.cash_intra.get(symbol) or [], day, until, after_hm=after
        )

    def cash_today(self, symbol: str, day: date) -> Candle | None:
        return self.cash_bar(symbol, day, (15, 15))

    def cash_yesterday(self, symbol: str, day: date) -> Candle | None:
        rows = self.cash_daily.get(symbol) or []
        prior = [row for row in rows if row[0] < day.isoformat()]
        if not prior:
            return None
        last = prior[-1]
        return Candle(last[0], last[1], last[2], last[3], last[4])


def trading_days(feed: ReplayFeed, symbols: list[str]) -> list[date]:
    dates: set[str] = set()
    for symbol in symbols[:30]:
        for row in feed.cash_daily.get(symbol) or []:
            day_s = row[0]
            try:
                day = date.fromisoformat(day_s)
            except ValueError:
                continue
            if START <= day <= END:
                dates.add(day_s)
        for bar in feed.cash_intra.get(symbol) or []:
            day = bar[0].date()
            if START <= day <= END:
                dates.add(day.isoformat())
    return [date.fromisoformat(d) for d in sorted(dates)]


def needs_futures_quote(
    feed: ReplayFeed,
    symbol: str,
    day: date,
    config,
) -> bool:
    """Skip the NFO candle call unless cash is already close to a take."""
    yesterday = feed.cash_yesterday(symbol, day)
    today_bar = feed.cash_today(symbol, day)
    if today_bar is None or yesterday is None:
        return False
    ohlc = feed.cash_daily.get(symbol) or []
    cfg = config.candles
    call_th = config.rsi.call_threshold
    put_th = config.rsi.put_threshold
    period = config.rsi.period
    y_rsi = rsi_on(cash_closes_before(ohlc, day), period)
    if reversal_setup(
        yesterday,
        today_bar,
        y_rsi,
        call_threshold=call_th,
        put_threshold=put_th,
        cfg=cfg,
    ):
        return True
    if waiting_reason(
        yesterday,
        y_rsi,
        call_threshold=call_th,
        put_threshold=put_th,
        cfg=cfg,
    ):
        return True
    rsi_close = rsi_on(cash_closes_with_last(ohlc, day, today_bar.close), period)
    rsi_high = rsi_on(cash_closes_with_last(ohlc, day, today_bar.high), period)
    rsi_low = rsi_on(cash_closes_with_last(ohlc, day, today_bar.low), period)
    if same_day_setup(
        today_bar,
        rsi_at_close=rsi_close,
        rsi_at_high=rsi_high,
        rsi_at_low=rsi_low,
        call_threshold=call_th,
        put_threshold=put_th,
        cfg=cfg,
    ):
        return True
    if CHART_ONLY:
        return False
    if rsi_high is not None and rsi_high >= call_th - 5:
        return True
    if rsi_low is not None and rsi_low <= put_th + 5:
        return True
    return False


def candle_signal(
    feed: ReplayFeed,
    symbol: str,
    day: date,
    fut_ltp: float,
    config,
) -> ScanAlert | None:
    ohlc = feed.cash_daily.get(symbol) or []
    yesterday = feed.cash_yesterday(symbol, day)
    today_bar = feed.cash_today(symbol, day)
    if today_bar is None or yesterday is None:
        return None
    if not CHART_ONLY:
        today_bar = with_live_close(today_bar, fut_ltp)
    cfg = config.candles
    call_th = config.rsi.call_threshold
    put_th = config.rsi.put_threshold
    period = config.rsi.period

    y_rsi = rsi_on(cash_closes_before(ohlc, day), period)
    setup = reversal_setup(
        yesterday,
        today_bar,
        y_rsi,
        call_threshold=call_th,
        put_threshold=put_th,
        cfg=cfg,
    )
    if setup:
        signal, pattern = setup
        stop = candle_stop_price(
            signal, reversal=today_bar, prior=yesterday, same_day=False
        )
        return make_candle_alert(
            symbol=symbol,
            ltp=fut_ltp,
            rsi=y_rsi or 0.0,
            signal=signal,
            pattern=pattern,
            stop_price=stop,
        )

    rsi_close = rsi_on(cash_closes_with_last(ohlc, day, today_bar.close), period)
    rsi_high = rsi_on(cash_closes_with_last(ohlc, day, today_bar.high), period)
    rsi_low = rsi_on(cash_closes_with_last(ohlc, day, today_bar.low), period)
    if CHART_ONLY:
        rsi_high = rsi_close
        rsi_low = rsi_close
    setup = same_day_setup(
        today_bar,
        rsi_at_close=rsi_close,
        rsi_at_high=rsi_high,
        rsi_at_low=rsi_low,
        call_threshold=call_th,
        put_threshold=put_th,
        cfg=cfg,
    )
    if setup:
        signal, pattern = setup
        rsi = rsi_close or rsi_high or rsi_low or 0.0
        stop = candle_stop_price(
            signal, reversal=today_bar, prior=None, same_day=True
        )
        return make_candle_alert(
            symbol=symbol,
            ltp=fut_ltp,
            rsi=rsi,
            signal=signal,
            pattern=pattern,
            stop_price=stop,
        )
    return None


def smma_levels(
    feed: ReplayFeed,
    book: PaperBook,
    day: date,
    fut_closes: dict[str, float],
) -> dict[str, tuple[float | None, float | None]]:
    if book.config.smma_fast is None or book.config.smma_slow is None:
        return {}
    levels: dict[str, tuple[float | None, float | None]] = {}
    for position in book.positions:
        if not position.is_open:
            continue
        ltp = fut_closes.get(position.symbol)
        if not ltp:
            continue
        ohlc = feed.cash_daily.get(position.symbol) or []
        closes = cash_closes_with_last(ohlc, day, ltp)
        levels[position.symbol] = (
            smma_on(closes, book.config.smma_fast),
            smma_on(closes, book.config.smma_slow),
        )
    return levels


def rsi_values(
    feed: ReplayFeed,
    symbols: list[str],
    day: date,
    fut_closes: dict[str, float],
    period: int,
) -> dict[str, float]:
    out: dict[str, float] = {}
    for symbol in symbols:
        ltp = fut_closes.get(symbol)
        if not ltp:
            continue
        ohlc = feed.cash_daily.get(symbol) or []
        value = rsi_on(cash_closes_with_last(ohlc, day, ltp), period)
        if value is not None:
            out[symbol] = value
    return out


def apply_exits(
    book: PaperBook,
    fut_bars: dict[str, Candle],
    cash_bars: dict[str, Candle],
    day: date,
    rsi: dict[str, float],
    smma: dict[str, tuple[float | None, float | None]],
    *,
    no_stop: bool = False,
) -> list:
    """Candle stop = cash close through the cash-bar stop. SMMA/RSI on futures."""
    events = []
    for position in list(book.positions):
        if not position.is_open:
            continue
        cash = cash_bars.get(position.symbol)
        fut = fut_bars.get(position.symbol)
        rsi_px = rsi.get(position.symbol)
        levels = smma.get(position.symbol)

        if not no_stop and cash is not None:
            cash_stop = book._candle_stop_fill(position, cash.close)
            if cash_stop is not None:
                events.append(
                    book._close_lots(
                        position,
                        position.lots_open,
                        cash_stop,
                        ExitReason.STOP_LOSS,
                        rsi_px,
                    )
                )
                continue

        if fut is None:
            continue
        short = position.direction == Direction.SHORT
        favor = fut.low if short else fut.high
        close = fut.close
        if book._uses_smma_targets():
            events.extend(book._apply_smma_exits(position, favor, rsi_px, levels))
            if position.is_open:
                events.extend(
                    book._apply_smma_exits(position, close, rsi_px, levels)
                )
        if position.is_open and position.expiry and str(day) >= position.expiry:
            events.append(
                book._close_lots(
                    position,
                    position.lots_open,
                    close,
                    ExitReason.EXPIRY,
                    rsi_px,
                )
            )
    book.positions = [p for p in book.positions if p.is_open]
    return events


def snapshot_open(book: PaperBook, marks: dict[str, float]) -> list[dict]:
    rows = []
    for position in book.positions:
        if not position.is_open:
            continue
        mark = marks.get(position.symbol)
        running = position.unrealised(mark) if mark else None
        rows.append(
            {
                "symbol": position.symbol,
                "side": position.direction,
                "entry": round(position.entry_price, 2),
                "entry_time": position.entry_time,
                "lots_open": position.lots_open,
                "lots_total": position.lots_total,
                "lot_size": position.lot_size,
                "expiry": position.expiry,
                "stop": position.stop_price,
                "mark": None if mark is None else round(mark, 2),
                "running_pnl": None if running is None else round(running, 2),
                "booked_on_trade": round(position.realised, 2),
                "rsi_at_entry": round(position.rsi_at_entry, 1),
            }
        )
    return rows


def main() -> None:
    started = time()
    resume = "--resume" in sys.argv
    config = load_config(ROOT / "config.yaml")
    paper = replace(
        config.paper_trading,
        futures_month=FUTURES_MONTH,
        ledger_path=str(LEDGER),
        journal_csv=str(JOURNAL),
        google_sheet_id="",
        google_worksheet="",
        google_summary_worksheet="",
        name="RSI_CandlePattern 3rd-month 2w",
    )
    if not resume:
        for path in (LEDGER, JOURNAL):
            if path.exists():
                path.unlink()

    prior = {}
    if resume and RESULT.exists():
        prior = json.loads(RESULT.read_text(encoding="utf-8"))

    client = AngelOneClient(
        rsi_period=config.rsi.period,
        history_days=config.data.history_days,
        extreme_history_days=config.data.extreme_history_days,
    )
    feed = ReplayFeed(client, config.data.history_days)
    book = PaperBook(paper, path=LEDGER, journal=None, no_short_symbols=config.no_short_symbols)

    try:
        symbols = client.fno_symbols()
        print(
            f"RSI_CandlePattern replay {START} → {END} · 3rd-month futures · "
            f"15:15 IST · {len(symbols)} F&O names"
            + (" · chart setups only" if CHART_ONLY else "")
            + (" · resume" if resume else "")
        )
        feed.load_cash_daily(symbols)
        feed.load_cash_intra(symbols)
        days = trading_days(feed, symbols)
        done = {row["date"] for row in (prior.get("equity_curve") or [])}
        if resume:
            days = [day for day in days if day.isoformat() not in done]
        print(f"Sessions: {', '.join(d.isoformat() for d in days) or '(none)'}")

        equity_curve: list[dict] = list(prior.get("equity_curve") or [])
        entries_log: list[dict] = list(prior.get("entries") or [])
        exits_log: list[dict] = list(prior.get("exits") or [])

        for day in days:
            book._roll_day(day)
            set_clock(day, ENTRY_STAMP)
            skip_new = bool(expiry_entry_skip_reason(day))
            if skip_new:
                print(f"\n{day} 15:15 — no new entries ({expiry_entry_skip_reason(day)})")
            else:
                print(f"\n{day} 15:15 — scan + mark")

            open_syms = [p.symbol for p in book.positions if p.is_open]
            bars_1515: dict[str, Candle] = {}
            cash_1515: dict[str, Candle] = {}
            for symbol in open_syms:
                entry_day = date.fromisoformat(p_entry_date(book, symbol))
                bar = feed.fut_bar(
                    symbol, entry_day, (15, 15), session_day=day
                )
                if bar:
                    bars_1515[symbol] = bar
                cash = feed.cash_bar(symbol, day, (15, 15))
                if cash:
                    cash_1515[symbol] = cash

            marks = {sym: bar.close for sym, bar in bars_1515.items()}
            smma = smma_levels(feed, book, day, marks)
            rsi = rsi_values(feed, open_syms, day, marks, config.rsi.period)
            for event in apply_exits(
                book, bars_1515, cash_1515, day, rsi, smma
            ):
                exits_log.append(
                    {
                        "day": day.isoformat(),
                        "stamp": ENTRY_STAMP,
                        "symbol": event.symbol,
                        "kind": event.kind,
                        "detail": event.detail,
                        "pnl": round(event.pnl, 2),
                    }
                )
                print(f"  exit {event.symbol}: {event.kind} {event.detail} P&L ₹{event.pnl:,.0f}")

            new_alerts: list[ScanAlert] = []
            if not skip_new:
                held = {p.symbol for p in book.positions if p.is_open}
                for index, symbol in enumerate(symbols, 1):
                    if symbol in held:
                        continue
                    if not needs_futures_quote(feed, symbol, day, config):
                        continue
                    fut = feed.fut_bar(symbol, day, (15, 15), session_day=day)
                    if not fut:
                        continue
                    alert = candle_signal(feed, symbol, day, fut.close, config)
                    if not alert or alert.skip_reason:
                        continue
                    blocked = no_short_skip_reason(
                        symbol,
                        config.no_short_symbols,
                        is_short=alert.signal is SignalType.RSI_CANDLE_SHORT,
                    )
                    if blocked:
                        continue
                    data = feed.contract_data(symbol, day)
                    if not data:
                        continue
                    alert.ltp = fut.close
                    alert.expiry = data.contract.expiry
                    alert.lot_size = data.contract.lot_size
                    alert.message = (
                        f"{symbol}: {alert.candle_pattern} @ fut ₹{fut.close:,.2f} "
                        f"({data.contract.nfo_symbol})"
                    )
                    new_alerts.append(alert)
                    if index % 50 == 0:
                        print(f"  screened {index}/{len(symbols)}")

            set_clock(day, ENTRY_STAMP)
            for event in book.open_from_alerts(new_alerts):
                if event.kind != "entry":
                    print(f"  skip {event.symbol}: {event.detail}")
                    continue
                entries_log.append(
                    {
                        "day": day.isoformat(),
                        "symbol": event.symbol,
                        "detail": event.detail,
                    }
                )
                print(f"  entry {event.symbol}: {event.detail}")

            set_clock(day, EXIT_STAMP)
            open_positions = [p for p in book.positions if p.is_open]
            bars_1530: dict[str, Candle] = {}
            cash_1530: dict[str, Candle] = {}
            for position in open_positions:
                entry_day = date.fromisoformat(position.entry_time[:10])
                opened_today = position.entry_time[:10] == day.isoformat()
                after = (15, 15) if opened_today else None
                bar = feed.fut_bar(
                    position.symbol,
                    entry_day,
                    (15, 31),
                    session_day=day,
                    after=after,
                )
                if bar:
                    bars_1530[position.symbol] = bar
                cash = feed.cash_bar(
                    position.symbol, day, (15, 31), after=after
                )
                if cash:
                    cash_1530[position.symbol] = cash
            open_syms = [p.symbol for p in open_positions]
            marks = {sym: bar.close for sym, bar in bars_1530.items()}
            smma = smma_levels(feed, book, day, marks)
            rsi = rsi_values(feed, open_syms, day, marks, config.rsi.period)
            for event in apply_exits(
                book, bars_1530, cash_1530, day, rsi, smma
            ):
                exits_log.append(
                    {
                        "day": day.isoformat(),
                        "stamp": EXIT_STAMP,
                        "symbol": event.symbol,
                        "kind": event.kind,
                        "detail": event.detail,
                        "pnl": round(event.pnl, 2),
                    }
                )
                print(f"  15:30 exit {event.symbol}: {event.kind} P&L ₹{event.pnl:,.0f}")

            marks = {sym: bar.close for sym, bar in bars_1530.items()}
            for position in book.positions:
                if position.is_open and position.symbol not in marks:
                    marks[position.symbol] = position.entry_price
            running = book.unrealised(marks)
            booked = book.realised_pnl
            equity_curve.append(
                {
                    "date": day.isoformat(),
                    "booked_pnl": round(booked, 2),
                    "running_pnl": round(running, 2),
                    "total_pnl": round(booked + running, 2),
                    "open_positions": len([p for p in book.positions if p.is_open]),
                }
            )
            print(
                f"  booked ₹{booked:,.0f} · running ₹{running:,.0f} · "
                f"total ₹{booked + running:,.0f} · open {len(book.positions)}"
            )

        set_clock(END, ENTRY_STAMP)
        final_marks: dict[str, float] = {}
        for position in book.positions:
            if not position.is_open:
                continue
            entry_day = date.fromisoformat(position.entry_time[:10])
            last_day = days[-1] if days else END
            bar = feed.fut_bar(
                position.symbol, entry_day, (15, 15), session_day=last_day
            )
            if bar:
                final_marks[position.symbol] = bar.close
        running = book.unrealised(final_marks)
        booked = book.realised_pnl
        book.save()

        payload = {
            "title": "RSI_CandlePattern · 3rd-month futures · 19 Aug–2 Sep 2026",
            "entry_time": "15:15 IST",
            "mark_time": "15:15 IST (15:30 stop/SMMA pass after entries)",
            "futures_month": FUTURES_MONTH,
            "capital": paper.capital,
            "sessions": [d.isoformat() for d in days],
            "booked_pnl": round(booked, 2),
            "running_pnl": round(running, 2),
            "total_pnl": round(booked + running, 2),
            "closed_count": book.closed_count,
            "open_count": len([p for p in book.positions if p.is_open]),
            "equity_curve": equity_curve,
            "open_positions": snapshot_open(book, final_marks),
            "entries": entries_log,
            "exits": exits_log,
            "elapsed_sec": round(time() - started, 1),
        }
        RESULT.parent.mkdir(parents=True, exist_ok=True)
        RESULT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(
            f"\nDone in {payload['elapsed_sec']}s. Booked ₹{booked:,.0f} · "
            f"running ₹{running:,.0f} · total ₹{booked + running:,.0f}"
        )
        print(f"Wrote {RESULT}")
    finally:
        client.close()


def p_entry_date(book: PaperBook, symbol: str) -> str:
    for position in book.positions:
        if position.symbol == symbol and position.is_open:
            return position.entry_time[:10]
    return date.today().isoformat()


if __name__ == "__main__":
    main()
