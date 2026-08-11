from __future__ import annotations

from datetime import datetime, time as dt_time

from src.config import AppConfig, SignalType
from src.data.angelone_client import AngelOneClient
from src.data.models import PriceSnapshot
from src.notifications.notifier import Notifier
from src.oi_analyzer import (
    ScanAlert,
    call_oi_flow_rejection,
    evaluate_stock,
    matched_signal,
    put_oi_flow_rejection,
)
from src.paper_trading import PaperBook
from src.paper_trading.journal import TradeJournal


class OIRsiScanner:
    def __init__(self, config: AppConfig):
        self.config = config
        self.client = AngelOneClient(
            rsi_period=config.rsi.period,
            history_days=config.data.history_days,
        )
        self.notifier = Notifier(config.notifications)
        self.book = None
        if config.paper_trading.enabled:
            paper = config.paper_trading
            journal = TradeJournal(
                csv_path=paper.journal_csv,
                sheet_id=paper.google_sheet_id,
                worksheet=paper.google_worksheet,
            )
            self.book = PaperBook(paper, journal=journal)

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

    def _rsi_candidates(
        self, symbols: list[str], prices: dict[str, float], rsi_values: dict[str, float]
    ) -> list[PriceSnapshot]:
        """Stocks whose RSI is stretched enough to be worth an option-chain lookup.

        Fills `rsi_values` for every symbol, since open positions need an exit RSI
        even when they are nowhere near alerting.
        """
        missing = [s for s in symbols if s not in prices]
        if missing:
            print(f"  no live price for {len(missing)} symbol(s): {', '.join(missing[:5])}")

        candidates: list[PriceSnapshot] = []
        for index, symbol in enumerate(symbols, 1):
            ltp = prices.get(symbol)
            if not ltp:
                continue

            rsi = self.client.get_rsi(symbol, ltp)
            if rsi is None:
                continue

            rsi_values[symbol] = rsi

            if rsi >= self.config.rsi.call_threshold or rsi <= self.config.rsi.put_threshold:
                candidates.append(PriceSnapshot(symbol=symbol, ltp=ltp, rsi=rsi))

            if index % 25 == 0:
                print(f"  screened {index}/{len(symbols)} symbols...")
                self.client._save_closes_cache()

        self.client._save_closes_cache()
        return candidates

    def _check_candidate(self, price: PriceSnapshot) -> ScanAlert | None:
        oi = self.client.get_oi_snapshot(price.symbol, ltp=price.ltp)
        if not oi:
            return None

        thresholds = dict(
            rsi_call_threshold=self.config.rsi.call_threshold,
            rsi_put_threshold=self.config.rsi.put_threshold,
            proximity_pct=self.config.oi.proximity_pct,
        )
        flow = dict(
            require_call_writing=self.config.oi.require_call_writing,
            max_change_pcr=self.config.oi.max_change_pcr,
            require_put_writing=self.config.oi.require_put_writing,
            min_change_pcr=self.config.oi.min_change_pcr,
        )

        # OI history costs two extra requests, so only price it in once the
        # stock has actually qualified on RSI + strike proximity.
        signal = matched_signal(price, oi, **thresholds)
        if signal is None:
            print(
                f"  {price.symbol}: RSI {price.rsi:.1f} qualifies but price ₹{price.ltp:.2f} "
                f"is not near max Call OI ₹{oi.max_call_oi_strike:.0f} "
                f"or max Put OI ₹{oi.max_put_oi_strike:.0f}"
            )
            return None

        self.client.add_oi_changes(oi)

        if signal is SignalType.CALL_OI:
            rejected = call_oi_flow_rejection(oi, **flow)
            if rejected:
                print(f"  {price.symbol}: CALL_OI skipped — {rejected}")
                return None
        else:
            rejected = put_oi_flow_rejection(oi, **flow)
            if rejected:
                print(f"  {price.symbol}: PUT_OI skipped — {rejected}")
                return None

        return evaluate_stock(price=price, oi=oi, **thresholds, **flow)

    def _apply_futures_expiry(self, alerts: list[ScanAlert]) -> None:
        """Point paper entries at the configured futures month (default: 3rd)."""
        month = self.config.paper_trading.futures_month
        for alert in alerts:
            contract = self.client.futures_contract(alert.symbol, month_index=month)
            if not contract:
                print(
                    f"  {alert.symbol}: no month-{month} stock future listed; "
                    "paper entry will keep the options expiry"
                )
                continue
            expiry, lot = contract
            alert.expiry = expiry
            if lot > 0:
                alert.lot_size = lot

    def _align_open_futures_expiry(self) -> None:
        """Roll open paper positions onto the configured futures month."""
        if not self.book:
            return

        month = self.config.paper_trading.futures_month
        changed = 0
        for position in self.book.positions:
            if not position.is_open:
                continue
            contract = self.client.futures_contract(position.symbol, month_index=month)
            if not contract:
                continue
            expiry, _lot = contract
            if position.expiry != expiry:
                position.expiry = expiry
                changed += 1

        if changed:
            print(f"Aligned {changed} open paper position(s) to month-{month} futures expiry.")

    def _run_paper_trading(
        self,
        alerts: list[ScanAlert],
        prices: dict[str, float],
        rsi_values: dict[str, float],
    ) -> None:
        if not self.book:
            return

        # Paper futures always use the configured month (default: 3rd / far month).
        # OI alerts still describe the nearest options expiry used for the signal.
        self._align_open_futures_expiry()
        self._apply_futures_expiry(alerts)

        # Exits are settled before entries so freed margin can fund new trades.
        events = self.book.update(prices, rsi_values=rsi_values)
        events += self.book.open_from_alerts(alerts)
        self.book.save()

        logged = self.book.flush_journal()
        if logged:
            print(f"\nLogged {logged} closed trade(s) to the journal.")

        if events:
            print("\nPaper trading")
            for event in events:
                pnl = f"  P&L ₹{event.pnl:+,.0f}" if event.pnl else ""
                print(f"  [{event.kind}] {event.symbol}: {event.detail}{pnl}")

        print()
        print(self.book.summary(prices))

        # Send a book snapshot whenever something changed or positions are open,
        # so Telegram always has live per-position and day P&L during the session.
        if self.notifier.telegram_ready and (events or self.book.positions):
            image = self.book.telegram_dashboard_image(prices, events)
            caption = self.book.telegram_report(prices, events)
            delivered = self.notifier.send_photo(
                image, caption=caption, parse_mode="HTML"
            )
            print(
                f"Sent positions dashboard to Telegram "
                f"({delivered}/{len(self.notifier.chat_ids)} recipient(s))."
            )

    def run_once(self) -> list[ScanAlert]:
        started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        symbols = self.symbols()
        print(f"\nScan started at {started} over {len(symbols)} stocks (live Angel One data)")

        # Prefer the committed seed so hosted runners do not refetch 208 candle
        # series and blow the Angel One rate limit.
        self.client.seed_closes_cache_from_repo()

        prices = self.client.get_ltps(symbols)
        rsi_values: dict[str, float] = {}
        candidates = self._rsi_candidates(symbols, prices, rsi_values)
        print(
            f"\n{len(candidates)} stock(s) passed the RSI filter "
            f"(>= {self.config.rsi.call_threshold} or <= {self.config.rsi.put_threshold})"
        )

        alerts: list[ScanAlert] = []
        for price in candidates:
            alert = self._check_candidate(price)
            if alert:
                alerts.append(alert)

        if alerts:
            self.notifier.notify(alerts)
        else:
            print("\nNo alerts this scan.")

        self._run_paper_trading(alerts, prices, rsi_values)
        return alerts
