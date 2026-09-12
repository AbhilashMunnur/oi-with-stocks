from __future__ import annotations

import math
from datetime import date, datetime, time as dt_time

from src.candle_patterns import (
    Candle,
    candle_stop_price,
    make_candle_alert,
    wider_of_candle_and_pct,
    reversal_setup,
    same_day_setup,
    waiting_reason,
    with_live_close,
)
from src.config import AppConfig, SignalType
from src.data.angelone_client import AngelOneClient
from src.data.option_expiry import expiry_entry_skip_reason, oi_scan_reason
from src.notifications.notifier import Notifier
from src.oi_analyzer import ScanAlert, no_short_skip_reason
from src.paper_trading import PaperBook
from src.paper_trading.journal import TradeJournal
from src.scan_slots import (
    is_cash_stop_slot,
    is_candle_screen_slot,
    is_close_pnl_slot,
    is_candle_entry_window,
)


class OIRsiScanner:
    def __init__(self, config: AppConfig):
        self.config = config
        self.client = AngelOneClient(
            rsi_period=config.rsi.period,
            history_days=config.data.history_days,
            extreme_history_days=config.data.extreme_history_days,
        )
        self.notifier = Notifier(config.notifications)
        self.two_week_book = None
        self.three_lot_book = None

        two_week = config.rsi_candle_2w_paper_trading
        if two_week and two_week.enabled:
            two_week_journal = TradeJournal(
                csv_path=two_week.journal_csv,
                sheet_id=two_week.google_sheet_id,
                worksheet=two_week.google_worksheet,
                summary_worksheet=two_week.google_summary_worksheet,
            )
            self.two_week_book = PaperBook(
                two_week,
                journal=two_week_journal,
                no_short_symbols=config.no_short_symbols,
                candle_cfg=config.candles,
            )

        three_lot = config.rsi_candle_3lot_paper_trading
        if three_lot and three_lot.enabled:
            three_lot_journal = TradeJournal(
                csv_path=three_lot.journal_csv,
                sheet_id=three_lot.google_sheet_id,
                worksheet=three_lot.google_worksheet,
                summary_worksheet=three_lot.google_summary_worksheet,
            )
            self.three_lot_book = PaperBook(
                three_lot,
                journal=three_lot_journal,
                no_short_symbols=config.no_short_symbols,
                candle_cfg=config.candles,
            )

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> OIRsiScanner:
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def _parse_hhmm(self, value: str) -> dt_time:
        hour, minute = map(int, value.split(":"))
        return dt_time(hour=hour, minute=minute)

    def is_market_hours(self, now: datetime | None = None) -> bool:
        now = now or datetime.now()
        if now.weekday() >= 5:
            return False

        start = self._parse_hhmm(self.config.schedule.market_start)
        end = self._parse_hhmm(self.config.schedule.market_end)
        return start <= now.time() <= end

    def symbols(self) -> list[str]:
        """The configured watchlist, or every F&O stock when set to 'all'."""
        watchlist = self.config.watchlist
        if isinstance(watchlist, str) and watchlist.strip().lower() == "all":
            return self.client.fno_symbols()
        return [symbol.upper() for symbol in watchlist]

    def _default_futures_month(self) -> int:
        paper = (
            self.config.rsi_candle_2w_paper_trading
            or self.config.rsi_candle_3lot_paper_trading
        )
        return paper.futures_month if paper else 3

    def _apply_futures_expiry(self, alerts: list[ScanAlert], month: int | None = None) -> None:
        """Point paper entries at this book's futures month (3rd-month stock futures)."""
        month = month if month is not None else self._default_futures_month()
        for alert in alerts:
            contract = self.client.futures_contract(alert.symbol, month_index=month)
            if not contract:
                print(
                    f"  {alert.symbol}: no month-{month} stock future listed; "
                    "paper entry will keep the options expiry"
                )
                continue
            expiry, lot = contract.expiry, contract.lot_size
            alert.expiry = expiry
            if lot > 0:
                alert.lot_size = lot

    def _align_open_futures_expiry(self, book: PaperBook) -> None:
        """Roll open paper positions onto the configured futures month."""
        target = book
        if not target:
            return

        month = target.config.futures_month
        changed = 0
        for position in target.positions:
            if not position.is_open:
                continue
            contract = self.client.futures_contract(position.symbol, month_index=month)
            if not contract:
                continue
            expiry, _lot = contract.expiry, contract.lot_size
            if position.expiry != expiry:
                position.expiry = expiry
                changed += 1

        if changed:
            print(
                f"Aligned {changed} {target.config.name} position(s) "
                f"to month-{month} futures expiry."
            )

    def _futures_paper_prices(
        self,
        book: PaperBook,
        alerts: list[ScanAlert],
    ) -> dict[str, float]:
        """LTP of the book's futures month. Cash is never used to mark P&L."""
        if book.config.mark_entry_contract:
            pairs = [
                (position.symbol, position.expiry)
                for position in book.positions
                if position.is_open and position.expiry
            ]
            if not book.config.skip_new_entries:
                pairs.extend(
                    (alert.symbol, alert.expiry)
                    for alert in alerts
                    if alert.expiry
                )
            if not pairs:
                return {}
            fut = self.client.get_futures_ltps_for_expiries(pairs)
            wanted = {symbol for symbol, _expiry in pairs}
            missing = sorted(symbol for symbol in wanted if symbol not in fut)
            if missing:
                print(
                    f"  {book.config.name}: no stored-expiry fut LTP for "
                    f"{', '.join(missing[:8])}"
                    + ("…" if len(missing) > 8 else "")
                )
            return fut

        symbols = {position.symbol for position in book.positions if position.is_open}
        symbols.update(alert.symbol for alert in alerts)
        if not symbols:
            return {}

        fut = self.client.get_futures_ltps(
            sorted(symbols), month_index=book.config.futures_month
        )
        missing = sorted(symbol for symbol in symbols if symbol not in fut)
        if missing:
            print(
                f"  {book.config.name}: no month-{book.config.futures_month} "
                f"fut LTP for {', '.join(missing[:8])}"
                + ("…" if len(missing) > 8 else "")
            )
        return fut

    def _smma_levels_for_book(
        self,
        book: PaperBook,
        cash_prices: dict[str, float],
    ) -> dict[str, tuple[float | None, float | None]] | None:
        """Cash SMMA 21 / 50 for open paper names, last bar = futures LTP.

        None when this book uses % targets. RSI_CandlePattern does not mark cash.
        """
        if book.config.smma_fast is None or book.config.smma_slow is None:
            return None
        levels: dict[str, tuple[float | None, float | None]] = {}
        for position in book.positions:
            if not position.is_open:
                continue
            ltp = cash_prices.get(position.symbol)
            levels[position.symbol] = (
                self.client.get_smma(position.symbol, book.config.smma_fast, ltp),
                self.client.get_smma(position.symbol, book.config.smma_slow, ltp),
            )
        return levels

    def _candles_for_book(
        self,
        book: PaperBook,
        cash_prices: dict[str, float],
    ) -> dict[str, Candle] | None:
        """Today's cash bar for open paper names, last close = cash LTP.

        Only the three-lot runner reads these, and only to ask whether a strong
        bar has closed back through SMMA 21. Both sides of that comparison are
        therefore on the same cash basis as `_smma_levels_for_book`.
        """
        if not (
            book.config.final_lot_smma_cross_exit or book.config.smma_reversal_exit
        ):
            return None
        today = f"{date.today():%Y-%m-%d}"
        bars: dict[str, Candle] = {}
        for position in book.positions:
            if not position.is_open:
                continue
            try:
                rows = self.client.daily_full_ohlc(position.symbol)
            except Exception:
                continue
            candles = self._bars_to_candles(rows)
            if not candles or candles[-1].date != today:
                continue
            ltp = cash_prices.get(position.symbol)
            bars[position.symbol] = (
                with_live_close(candles[-1], ltp) if ltp else candles[-1]
            )
        return bars

    def _restate_cash_entries(self, book: PaperBook) -> list:
        """Rewrite cash fills to the 3rd-month future print at entry time."""
        restated: dict[str, float] = {}
        for position in book.positions:
            if not position.is_open or position.priced_on == "futures":
                continue
            try:
                when = datetime.strptime(position.entry_time[:19], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                try:
                    when = datetime.strptime(position.entry_time[:10], "%Y-%m-%d")
                except ValueError:
                    continue
            close = self.client.futures_price_at(
                position.symbol, when, book.config.futures_month
            )
            if close:
                restated[position.symbol] = close
            else:
                print(
                    f"  {position.symbol}: no month-{book.config.futures_month} "
                    f"fut print at {position.entry_time}"
                )
        return book.rebase_entries_to_futures(restated)

    def _run_one_paper_book(
        self,
        book: PaperBook,
        alerts: list[ScanAlert],
        prices: dict[str, float],
        rsi_values: dict[str, float],
    ) -> None:
        if not book.config.mark_entry_contract:
            self._align_open_futures_expiry(book)
        if not book.config.skip_new_entries:
            self._apply_futures_expiry(alerts, month=book.config.futures_month)

        quote_alerts = [] if book.config.skip_new_entries else alerts
        fut_prices = self._futures_paper_prices(book, quote_alerts)
        events = self._restate_cash_entries(book)
        events += book.drop_void_positions(
            skip_monthly_expiry=self.config.oi.skip_monthly_expiry
        )

        paper_alerts: list[ScanAlert] = []
        if not book.config.skip_new_entries:
            for alert in alerts:
                if alert.skip_reason:
                    continue
                if alert.symbol not in fut_prices:
                    print(
                        f"  {alert.symbol}: not opening paper — "
                        f"no NSE month-{book.config.futures_month} futures LTP (cash is not used)"
                    )
                    continue
                alert.ltp = fut_prices[alert.symbol]
                paper_alerts.append(alert)

        smma_levels = self._smma_levels_for_book(book, fut_prices)
        cash_slot = is_cash_stop_slot()
        skip_candle = book.config.cash_close_stop and not cash_slot
        stop_prices = prices if book.config.cash_close_stop and cash_slot else None
        cash_for_bars = dict(prices)
        want_reversal = (
            book.config.smma_reversal_exit and is_candle_entry_window()
        )
        if want_reversal:
            missing = [
                p.symbol
                for p in book.positions
                if p.is_open and p.symbol not in cash_for_bars
            ]
            if missing:
                cash_for_bars.update(self.client.get_ltps(missing))
        if want_reversal:
            candles = self._candles_for_book(book, cash_for_bars)
        elif cash_slot:
            candles = self._candles_for_book(book, prices)
        else:
            candles = None
        reversal_smma = None
        reversal_smma_prev = None
        if want_reversal and candles is not None:
            period = book.config.smma_reversal or 9
            reversal_smma = {}
            reversal_smma_prev = {}
            for position in book.positions:
                if not position.is_open:
                    continue
                bar = candles.get(position.symbol)
                live = bar.close if bar else cash_for_bars.get(position.symbol)
                today_v, prev_v = self.client.get_smma_pair(
                    position.symbol, period, live
                )
                reversal_smma[position.symbol] = today_v
                reversal_smma_prev[position.symbol] = prev_v
        events += book.update(
            fut_prices,
            rsi_values=rsi_values,
            smma_levels=smma_levels,
            skip_candle_stop=skip_candle,
            stop_prices=stop_prices,
            # Live book still reads the SMMA 21 runner only at cash close.
            candles=candles,
            reversal_smma=reversal_smma,
            reversal_smma_prev=reversal_smma_prev,
        )
        if book.config.skip_new_entries:
            print(f"  {book.config.name}: marking open P&L — no new entries")
        elif not is_close_pnl_slot():
            events += book.open_from_alerts(paper_alerts)
        else:
            print(f"  {book.config.name}: 15:45 close — marking P&L, not opening new paper")
        book.save()

        logged = book.flush_journal()
        if logged:
            print(f"\nLogged {logged} {book.config.name} closed trade(s) to the journal.")

        if book.journal:
            book.journal.append_summary(book.portfolio_summary_row(fut_prices))

        if events:
            print(f"\n{book.config.name} paper trading")
            for event in events:
                pnl = f"  P&L ₹{event.pnl:+,.0f}" if event.pnl else ""
                print(f"  [{event.kind}] {event.symbol}: {event.detail}{pnl}")

        print()
        print(book.summary(fut_prices))

        closing = is_close_pnl_slot()
        send_dash = self.notifier.telegram_ready and (
            events or book.positions or closing
        )
        if send_dash:
            try:
                image = book.telegram_dashboard_image(
                    fut_prices, events, closing=closing
                )
                caption = book.telegram_report(
                    fut_prices, events, closing=closing
                )
                delivered = self.notifier.send_photo(
                    image, caption=caption, parse_mode="HTML"
                )
                print(
                    f"Sent {book.config.name} dashboard to Telegram "
                    f"({delivered}/{len(self.notifier.chat_ids)} recipient(s))."
                )
            except Exception as exc:
                print(f"  {book.config.name} dashboard Telegram failed: {exc}")

    def _emit_telegram(self, batch: list[ScanAlert], label: str) -> None:
        if not batch:
            return
        try:
            self.notifier.notify(batch)
            if self.notifier.telegram_ready:
                print(f"  {label} Telegram sent at {datetime.now():%H:%M:%S}")
        except Exception as exc:
            print(f"  {label} Telegram failed ({exc}); continuing scan")

    def _run_paper_trading(
        self,
        alerts: list[ScanAlert],
        prices: dict[str, float],
        rsi_values: dict[str, float],
    ) -> None:
        candle_alerts = [
            a
            for a in alerts
            if a.signal
            in (SignalType.RSI_CANDLE_SHORT, SignalType.RSI_CANDLE_LONG)
            and not a.skip_reason
        ]
        if self.two_week_book:
            self._run_one_paper_book(
                self.two_week_book, candle_alerts, prices, rsi_values
            )
        if self.three_lot_book:
            self._run_one_paper_book(
                self.three_lot_book, candle_alerts, prices, rsi_values
            )

    def _bars_to_candles(
        self, rows: list[tuple[str, float, float, float, float]]
    ) -> list[Candle]:
        bars: list[Candle] = []
        for day, open_, high, low, close in rows:
            if not math.isfinite(open_) or open_ <= 0:
                continue
            bars.append(
                Candle(date=day, open=open_, high=high, low=low, close=close)
            )
        return bars

    def _candle_entry_alert(
        self,
        *,
        symbol: str,
        ltp: float,
        rsi: float,
        signal: SignalType,
        pattern: str,
        detail: str,
        stop_price: float | None = None,
    ) -> ScanAlert:
        paper = self.config.rsi_candle_2w_paper_trading
        shown_stop = stop_price
        if paper is None or not paper.candle_stop:
            stop_price = None
            shown_stop = None
        elif stop_price is not None:
            shown_stop = wider_of_candle_and_pct(
                "SHORT" if signal is SignalType.RSI_CANDLE_SHORT else "LONG",
                ltp,
                stop_price,
                paper.stop_loss_pct,
            )
        blocked = no_short_skip_reason(
            symbol,
            self.config.no_short_symbols,
            is_short=signal is SignalType.RSI_CANDLE_SHORT,
        )
        if blocked:
            print(f"  {symbol}: {pattern} skipped — {blocked}")
            return make_candle_alert(
                symbol=symbol,
                ltp=ltp,
                rsi=rsi,
                signal=signal,
                pattern=pattern,
                skip_reason=blocked,
                stop_price=stop_price,
            )
        if self.config.oi.skip_monthly_expiry:
            expiry_skip = expiry_entry_skip_reason()
            if expiry_skip:
                print(f"  {symbol}: {pattern} skipped — {expiry_skip}")
                return make_candle_alert(
                    symbol=symbol,
                    ltp=ltp,
                    rsi=rsi,
                    signal=signal,
                    pattern=pattern,
                    skip_reason=expiry_skip,
                    stop_price=stop_price,
                )
        stop_txt = f", stop ₹{shown_stop:,.2f}" if shown_stop else ""
        print(f"  {symbol}: {signal.value} {pattern} ({detail}{stop_txt})")
        return make_candle_alert(
            symbol=symbol,
            ltp=ltp,
            rsi=rsi,
            signal=signal,
            pattern=pattern,
            stop_price=stop_price,
        )

    def _check_candle_reversal(
        self, symbol: str, ltp: float
    ) -> tuple[ScanAlert | None, str | None]:
        """RSI_CandlePattern: take if either scenario qualifies, from 15:15 IST.

        Next-day: yesterday's stretch + today's reversal candle.
        Same-day: RSI tagged 70/30 today and the bar already reversed.
        Live price is 3rd-month futures, never cash.
        """
        try:
            rows = self.client.daily_full_ohlc(symbol)
        except Exception as exc:
            print(f"  {symbol}: candles unavailable ({exc})")
            return None, None

        bars = self._bars_to_candles(rows)
        today = f"{date.today():%Y-%m-%d}"
        if not bars or bars[-1].date != today:
            return None, None

        today_bar = with_live_close(bars[-1], ltp)
        cfg = self.config.candles
        call_th = self.config.rsi.call_threshold
        put_th = self.config.rsi.put_threshold
        yesterday_rsi: float | None = None
        take = is_candle_entry_window()

        if take and len(bars) >= 2:
            yesterday = bars[-2]
            yesterday_rsi = self.client.completed_rsi(symbol)
            setup = reversal_setup(
                yesterday,
                today_bar,
                yesterday_rsi,
                call_threshold=call_th,
                put_threshold=put_th,
                cfg=cfg,
            )
            if setup:
                signal, pattern = setup
                stop = candle_stop_price(
                    signal, reversal=today_bar, prior=yesterday, same_day=False
                )
                return (
                    self._candle_entry_alert(
                        symbol=symbol,
                        ltp=ltp,
                        rsi=yesterday_rsi or 0.0,
                        signal=signal,
                        pattern=pattern,
                        detail=f"next-day, yesterday RSI {yesterday_rsi:.1f}",
                        stop_price=stop,
                    ),
                    None,
                )

        if take:
            rsi_close = self.client.get_rsi(symbol, ltp)
            rsi_high = self.client.get_rsi(symbol, today_bar.high)
            rsi_low = self.client.get_rsi(symbol, today_bar.low)
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
                return (
                    self._candle_entry_alert(
                        symbol=symbol,
                        ltp=ltp,
                        rsi=rsi,
                        signal=signal,
                        pattern=pattern,
                        detail=f"same-day by 15:15, RSI {rsi:.1f}",
                        stop_price=stop,
                    ),
                    None,
                )
        elif len(bars) >= 2:
            yesterday_rsi = self.client.completed_rsi(symbol)

        if len(bars) >= 2:
            waiting = waiting_reason(
                bars[-2],
                yesterday_rsi,
                call_threshold=call_th,
                put_threshold=put_th,
                cfg=cfg,
            )
            if waiting and yesterday_rsi is not None and yesterday_rsi >= call_th:
                return None, "short"
            if waiting:
                return None, "long"
        return None, None

    def _open_paper_symbols(self) -> list[str]:
        names: set[str] = set()
        for book in (self.two_week_book, self.three_lot_book):
            if not book:
                continue
            names.update(p.symbol for p in book.positions if p.is_open)
        return sorted(names)

    def _mark_open_books(self) -> list[ScanAlert]:
        """Mark open paper to futures LTP and Telegram P&L — no 210-name screen.

        New RSI_CandlePattern fills are 15:15 only. Screening every F&O name
        on the morning slots was blowing the 12-minute cap before Telegram.
        """
        print("  Mark-only slot — open P&L, not screening new candle entries")
        open_names = self._open_paper_symbols()
        prices: dict[str, float] = {}
        if open_names and is_cash_stop_slot():
            prices = self.client.get_ltps(open_names)
        # Do not call completed_rsi here — that refetches daily candles when
        # the cache is cold and blows the Angel rate limit before Telegram.
        # Lot-2 RSI 30/70 waits until the 15:15 screen.
        if not open_names:
            print("  No open paper to mark.")
        self._run_paper_trading([], prices, {})
        return []

    def run_once(self) -> list[ScanAlert]:
        started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        symbols = self.symbols()
        print(f"\nScan started at {started} over {len(symbols)} stocks (live Angel One data)")
        print(f"  {oi_scan_reason()}")

        # Prefer the committed seed so hosted runners do not refetch 208 candle
        # series and blow the Angel One rate limit.
        self.client.seed_closes_cache_from_repo()

        if not is_candle_screen_slot():
            return self._mark_open_books()

        prices: dict[str, float] = {}
        open_names = self._open_paper_symbols()
        if open_names and is_cash_stop_slot():
            prices = self.client.get_ltps(open_names)

        fut_month = self._default_futures_month()
        fut_scan = self.client.get_futures_ltps(symbols, month_index=fut_month)
        print(
            f"  RSI_CandlePattern: month-{fut_month} futures LTP "
            f"on {len(fut_scan)}/{len(symbols)} names — cash not used for fills"
        )
        if not is_candle_entry_window():
            print("  RSI_CandlePattern: before 15:15 IST — no new entries")

        rsi_values: dict[str, float] = {}
        alerts: list[ScanAlert] = []

        call_th = self.config.rsi.call_threshold
        put_th = self.config.rsi.put_threshold
        print(
            f"\nRSI_CandlePattern — take if either next-day or same-day qualifies "
            f"(from 15:15 IST, month-{fut_month} futures)"
        )
        print(
            f"  short after RSI ≥ {call_th:g} (any candle) + next/same-day "
            "inverted hammer / weak middle / strong red"
        )
        print(
            f"  long after RSI ≤ {put_th:g} (any candle) + next/same-day "
            "hammer / weak middle / strong green"
        )

        waiting_short = waiting_long = 0
        hits = 0
        for index, symbol in enumerate(symbols, 1):
            ltp = fut_scan.get(symbol)
            if not ltp:
                continue
            rsi = self.client.get_rsi(symbol, ltp)
            if rsi is not None:
                rsi_values[symbol] = rsi
            alert, waiting = self._check_candle_reversal(symbol, ltp)
            if waiting == "short":
                waiting_short += 1
            elif waiting == "long":
                waiting_long += 1
            if alert:
                alerts.append(alert)
                hits += 1
            if index % 25 == 0:
                print(f"  screened {index}/{len(symbols)} symbols...")
                self.client._save_ohlc_cache()
                self.client._save_closes_cache()

        self.client._save_ohlc_cache()
        self.client._save_closes_cache()
        print(f"  {hits} reversal signal(s)")
        print(
            f"  {waiting_short} name(s) RSI ≥ {call_th:g} strong bull "
            "— not shorting until a reversal candle"
        )
        print(
            f"  {waiting_long} name(s) RSI ≤ {put_th:g} strong bear "
            "— not longing until a reversal candle"
        )

        candle_batch = [
            a
            for a in alerts
            if a.signal
            in (SignalType.RSI_CANDLE_SHORT, SignalType.RSI_CANDLE_LONG)
        ]
        if is_close_pnl_slot():
            print("  15:45 close — skipping signal Telegram; sending closing P&L")
        else:
            self._emit_telegram(candle_batch, "RSI_CandlePattern")

        if not alerts:
            print("\nNo alerts this scan.")

        self._run_paper_trading(alerts, prices, rsi_values)
        return alerts
