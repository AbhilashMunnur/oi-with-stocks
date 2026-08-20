from __future__ import annotations

from datetime import datetime, time as dt_time

from src.config import AppConfig, SignalType
from src.data.angelone_client import AngelOneClient
from src.data.models import PriceSnapshot
from src.notifications.notifier import Notifier
from src.oi_analyzer import (
    ScanAlert,
    align_snapshot_to_reference_strike,
    call_oi_flow_rejection,
    evaluate_stock,
    matched_signal,
    put_oi_flow_rejection,
)
from src.paper_trading import PaperBook
from src.paper_trading.journal import TradeJournal
from src.supertrend_oi import evaluate_supertrend_oi, fetch_supertrends


class OIRsiScanner:
    def __init__(self, config: AppConfig):
        self.config = config
        self.client = AngelOneClient(
            rsi_period=config.rsi.period,
            history_days=config.data.history_days,
        )
        self.notifier = Notifier(config.notifications)
        self.book = None
        self.st_book = None
        if config.paper_trading.enabled:
            paper = config.paper_trading
            journal = TradeJournal(
                csv_path=paper.journal_csv,
                sheet_id=paper.google_sheet_id,
                worksheet=paper.google_worksheet,
                summary_worksheet=paper.google_summary_worksheet,
            )
            self.book = PaperBook(paper, journal=journal)

        st_paper = config.supertrend_paper_trading
        if st_paper and st_paper.enabled:
            st_journal = TradeJournal(
                csv_path=st_paper.journal_csv,
                sheet_id=st_paper.google_sheet_id,
                worksheet=st_paper.google_worksheet,
                summary_worksheet=st_paper.google_summary_worksheet,
            )
            self.st_book = PaperBook(st_paper, journal=st_journal)

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
                f"is not near active Call OI ₹{oi.max_call_oi_strike:.0f} "
                f"or active Put OI ₹{oi.max_put_oi_strike:.0f}"
            )
            return None

        # Both CE and PE ΔOI at the same reference strike:
        # CALL → active Call OI wall (strike ≥ spot); PUT → active Put OI wall (strike ≤ spot).
        reference = "call" if signal is SignalType.CALL_OI else "put"
        if not align_snapshot_to_reference_strike(oi, reference):
            print(
                f"  {price.symbol}: {signal.value} skipped — "
                f"CE/PE missing at reference strike"
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

    def _check_supertrend_candidate(
        self,
        symbol: str,
        ltp: float,
        st_value: float,
        side: str,
        already_alerted: set[str],
    ) -> ScanAlert | None:
        """OI confirmation at the Supertrend strike for a near-ST name."""
        if symbol in already_alerted:
            return None

        st_cfg = self.config.supertrend
        distance = abs(ltp - st_value) / st_value * 100
        if distance > st_cfg.proximity_pct:
            return None
        if side == "below" and ltp >= st_value:
            return None
        if side == "above" and ltp <= st_value:
            return None

        oi = self.client.get_oi_at_price(symbol, target_price=st_value, ltp=ltp)
        if not oi:
            return None
        self.client.add_oi_changes(oi)

        alert = evaluate_supertrend_oi(
            symbol=symbol,
            ltp=ltp,
            supertrend=st_value,
            side=side,
            oi=oi,
            st_config=st_cfg,
            oi_config=self.config.oi,
        )
        if alert is None:
            print(
                f"  {symbol}: near ST ₹{st_value:,.2f} ({side}, {distance:.2f}%) "
                "but ΔOI flow does not confirm"
            )
        return alert

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

    def _align_open_futures_expiry(self, book: PaperBook | None = None) -> None:
        """Roll open paper positions onto the configured futures month."""
        target = book if book is not None else self.book
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
            expiry, _lot = contract
            if position.expiry != expiry:
                position.expiry = expiry
                changed += 1

        if changed:
            print(
                f"Aligned {changed} {target.config.name} position(s) "
                f"to month-{month} futures expiry."
            )

    def _run_one_paper_book(
        self,
        book: PaperBook,
        alerts: list[ScanAlert],
        prices: dict[str, float],
        rsi_values: dict[str, float],
    ) -> None:
        self._align_open_futures_expiry(book)
        self._apply_futures_expiry(alerts)

        events = book.update(prices, rsi_values=rsi_values)
        events += book.open_from_alerts(alerts)
        book.save()

        logged = book.flush_journal()
        if logged:
            print(f"\nLogged {logged} {book.config.name} closed trade(s) to the journal.")

        if book.journal:
            book.journal.append_summary(book.portfolio_summary_row(prices))

        if events:
            print(f"\n{book.config.name} paper trading")
            for event in events:
                pnl = f"  P&L ₹{event.pnl:+,.0f}" if event.pnl else ""
                print(f"  [{event.kind}] {event.symbol}: {event.detail}{pnl}")

        print()
        print(book.summary(prices))

        if self.notifier.telegram_ready and (events or book.positions):
            image = book.telegram_dashboard_image(prices, events)
            caption = book.telegram_report(prices, events)
            delivered = self.notifier.send_photo(
                image, caption=caption, parse_mode="HTML"
            )
            print(
                f"Sent {book.config.name} dashboard to Telegram "
                f"({delivered}/{len(self.notifier.chat_ids)} recipient(s))."
            )

    def _run_paper_trading(
        self,
        alerts: list[ScanAlert],
        prices: dict[str, float],
        rsi_values: dict[str, float],
    ) -> None:
        rsi_alerts = [
            a for a in alerts if a.signal in (SignalType.CALL_OI, SignalType.PUT_OI)
        ]
        st_alerts = [
            a
            for a in alerts
            if a.signal in (SignalType.ST_BEARISH, SignalType.ST_BULLISH)
        ]

        if self.book:
            self._run_one_paper_book(self.book, rsi_alerts, prices, rsi_values)
        if self.st_book:
            self._run_one_paper_book(self.st_book, st_alerts, prices, rsi_values)

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

        if self.config.supertrend.enabled:
            st_cfg = self.config.supertrend
            print(
                f"\nSupertrend scan ({st_cfg.atr_period}, {st_cfg.multiplier}) — "
                f"proximity {st_cfg.proximity_pct}%"
            )
            st_map = fetch_supertrends(
                symbols,
                prices,
                atr_period=st_cfg.atr_period,
                multiplier=st_cfg.multiplier,
            )
            near = []
            for symbol, (st_value, side) in st_map.items():
                ltp = prices.get(symbol)
                if not ltp:
                    continue
                distance = abs(ltp - st_value) / st_value * 100
                if distance <= st_cfg.proximity_pct:
                    if side == "below" and ltp < st_value:
                        near.append((symbol, ltp, st_value, side, distance))
                    elif side == "above" and ltp > st_value:
                        near.append((symbol, ltp, st_value, side, distance))

            print(f"  {len(near)} name(s) within {st_cfg.proximity_pct}% of Supertrend")
            st_hits = 0
            st_alerted: set[str] = set()
            for symbol, ltp, st_value, side, _distance in near:
                alert = self._check_supertrend_candidate(
                    symbol, ltp, st_value, side, st_alerted
                )
                if alert:
                    alerts.append(alert)
                    st_alerted.add(symbol)
                    st_hits += 1
            print(f"  {st_hits} Supertrend + OI alert(s)")

        if alerts:
            self.notifier.notify(alerts)
        else:
            print("\nNo alerts this scan.")

        self._run_paper_trading(alerts, prices, rsi_values)
        return alerts
