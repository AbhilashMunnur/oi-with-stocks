from __future__ import annotations

from datetime import datetime, time as dt_time

from src.config import AppConfig, SignalType
from src.data.angelone_client import AngelOneClient
from src.data.models import PriceSnapshot
from src.data.option_expiry import expiry_entry_skip_reason, oi_scan_reason
from src.notifications.notifier import Notifier
from src.oi_analyzer import (
    ScanAlert,
    align_snapshot_to_reference_strike,
    apply_oi_wall,
    call_oi_flow_rejection,
    choose_s1_entry_wall,
    copy_oi_snapshot,
    evaluate_stock,
    is_substantial_fallback_wall,
    make_rsi_alert,
    matched_signal,
    no_short_skip_reason,
    proximity_skip_reason,
    put_oi_flow_rejection,
    resistance_is_broken,
    retag_s1_alert_as_s2,
    rsi_watch_side,
    s2_confirm_invalidation,
    s2_invalidation_reason,
    s2_wall_still_valid,
    select_active_oi_walls,
    s2_size_skip_reason,
    support_is_broken,
)
from src.paper_trading import PaperBook
from src.paper_trading.journal import TradeJournal
from src.paper_trading.models import ExitReason
from src.scan_slots import is_close_pnl_slot, is_s1_wall_exit_slot
from src.supertrend_oi import evaluate_supertrend_oi, fetch_supertrends, make_supertrend_watch


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
        self.s1_book = None
        self.s2_book = None
        if config.paper_trading.enabled:
            paper = config.paper_trading
            journal = TradeJournal(
                csv_path=paper.journal_csv,
                sheet_id=paper.google_sheet_id,
                worksheet=paper.google_worksheet,
                summary_worksheet=paper.google_summary_worksheet,
            )
            self.book = PaperBook(
                paper, journal=journal, no_short_symbols=config.no_short_symbols
            )

        st_paper = config.supertrend_paper_trading
        if st_paper and st_paper.enabled:
            st_journal = TradeJournal(
                csv_path=st_paper.journal_csv,
                sheet_id=st_paper.google_sheet_id,
                worksheet=st_paper.google_worksheet,
                summary_worksheet=st_paper.google_summary_worksheet,
            )
            self.st_book = PaperBook(
                st_paper, journal=st_journal, no_short_symbols=config.no_short_symbols
            )

        s1_paper = config.rsi_s1_paper_trading
        if s1_paper and s1_paper.enabled:
            s1_journal = TradeJournal(
                csv_path=s1_paper.journal_csv,
                sheet_id=s1_paper.google_sheet_id,
                worksheet=s1_paper.google_worksheet,
                summary_worksheet=s1_paper.google_summary_worksheet,
            )
            self.s1_book = PaperBook(
                s1_paper, journal=s1_journal, no_short_symbols=config.no_short_symbols
            )

        s2_paper = config.rsi_s2_paper_trading
        if s2_paper and s2_paper.enabled:
            s2_journal = TradeJournal(
                csv_path=s2_paper.journal_csv,
                sheet_id=s2_paper.google_sheet_id,
                worksheet=s2_paper.google_worksheet,
                summary_worksheet=s2_paper.google_summary_worksheet,
            )
            self.s2_book = PaperBook(
                s2_paper, journal=s2_journal, no_short_symbols=config.no_short_symbols
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

    def _rsi_thresholds(self) -> dict:
        return dict(
            rsi_call_threshold=self.config.rsi.call_threshold,
            rsi_put_threshold=self.config.rsi.put_threshold,
            proximity_pct=self.config.oi.proximity_pct,
        )

    def _oi_flow(self) -> dict:
        return dict(
            require_call_writing=self.config.oi.require_call_writing,
            max_change_pcr=self.config.oi.max_change_pcr,
            require_put_writing=self.config.oi.require_put_writing,
            min_change_pcr=self.config.oi.min_change_pcr,
        )

    def _check_candidate(self, price: PriceSnapshot, oi=None) -> ScanAlert | None:
        thresholds = self._rsi_thresholds()
        flow = self._oi_flow()
        side = rsi_watch_side(
            price, thresholds["rsi_call_threshold"], thresholds["rsi_put_threshold"]
        )
        if side is None:
            return None

        blocked = no_short_skip_reason(
            price.symbol,
            self.config.no_short_symbols,
            is_short=side is SignalType.CALL_OI,
        )
        if blocked:
            print(f"  {price.symbol}: {side.value} skipped — {blocked}")
            return make_rsi_alert(price, oi, side, skip_reason=blocked)

        if self.config.oi.skip_monthly_expiry:
            expiry_skip = expiry_entry_skip_reason()
            if expiry_skip:
                print(f"  {price.symbol}: {side.value} skipped — {expiry_skip}")
                return make_rsi_alert(price, oi, side, skip_reason=expiry_skip)

        oi = oi or self.client.get_oi_snapshot(price.symbol, ltp=price.ltp)
        if not oi:
            return make_rsi_alert(price, None, side, skip_reason="OI unavailable")

        # OI history costs two extra requests, so only price it in once the
        # stock is actually near the wall.
        too_far = proximity_skip_reason(price, oi, side, thresholds["proximity_pct"])
        if too_far:
            print(f"  {price.symbol}: RSI {price.rsi:.1f} — {too_far}")
            return make_rsi_alert(price, oi, side, skip_reason=too_far)

        reference = "call" if side is SignalType.CALL_OI else "put"
        if not align_snapshot_to_reference_strike(oi, reference):
            reason = "CE/PE missing at reference strike"
            print(f"  {price.symbol}: {side.value} skipped — {reason}")
            return make_rsi_alert(price, oi, side, skip_reason=reason)

        self.client.add_oi_changes(oi)

        if side is SignalType.CALL_OI:
            rejected = call_oi_flow_rejection(oi, **flow)
        else:
            rejected = put_oi_flow_rejection(oi, **flow)
        if rejected:
            print(f"  {price.symbol}: {side.value} skipped — {rejected}")
            return make_rsi_alert(price, oi, side, skip_reason=rejected)

        return evaluate_stock(price=price, oi=oi, **thresholds, **flow)

    def _s1_watch(
        self,
        price: PriceSnapshot,
        oi,
        side: SignalType,
        skip_reason: str | None = None,
    ) -> ScanAlert:
        alert = make_rsi_alert(price, oi, side, skip_reason=skip_reason)
        alert.signal = (
            SignalType.CALL_OI_S1 if side is SignalType.CALL_OI else SignalType.PUT_OI_S1
        )
        return alert

    def _check_scenario1_candidate(
        self,
        price: PriceSnapshot,
        oi,
        *,
        proximity_pct: float | None = None,
        pcr_band_strikes: int | None = None,
        log_tag: str = "S1",
        s2_mode: bool = False,
    ) -> ScanAlert | None:
        """RSI+OI entry on an uncrossed wall, with S1 broken-wall fallback.

        Always returns a CALL/PUT S1 row for Telegram. skip_reason is set when
        we do not take the trade. Never open on a peak strike price has already
        crossed — even if writing continues.
        """
        thresholds = self._rsi_thresholds()
        if proximity_pct is not None:
            thresholds["proximity_pct"] = proximity_pct
        flow = self._oi_flow()
        side = rsi_watch_side(
            price, thresholds["rsi_call_threshold"], thresholds["rsi_put_threshold"]
        )
        if side is None:
            return None

        blocked = no_short_skip_reason(
            price.symbol,
            self.config.no_short_symbols,
            is_short=side is SignalType.CALL_OI,
        )
        if blocked:
            print(f"  {price.symbol}: {log_tag} {side.value} skipped — {blocked}")
            return self._s1_watch(price, oi, side, blocked)

        if self.config.oi.skip_monthly_expiry:
            expiry_skip = expiry_entry_skip_reason()
            if expiry_skip:
                return self._s1_watch(price, oi, side, expiry_skip)

        if not oi or not oi.legs_by_strike:
            return self._s1_watch(price, None, side, "OI unavailable")

        s1 = copy_oi_snapshot(oi)
        min_pct = self.config.oi.s1_min_fallback_oi_pct
        peak_call, peak_put = select_active_oi_walls(s1.legs_by_strike, ltp=0)
        active_call, active_put = select_active_oi_walls(s1.legs_by_strike, price.ltp)

        if side is SignalType.CALL_OI and active_call and not is_substantial_fallback_wall(
            active_call, peak_call, min_pct
        ):
            peak = peak_call[0] if peak_call else 0
            reason = (
                f"fallback ₹{active_call[0]:.0f} too thin vs peak ₹{peak:.0f} "
                f"(need ≥ {min_pct:g}% of peak OI)"
            )
            print(f"  {price.symbol}: {log_tag} CALL skipped — {reason}")
            return self._s1_watch(price, s1, side, reason)
        if side is SignalType.PUT_OI and active_put and not is_substantial_fallback_wall(
            active_put, peak_put, min_pct
        ):
            peak = peak_put[0] if peak_put else 0
            reason = (
                f"fallback ₹{active_put[0]:.0f} too thin vs peak ₹{peak:.0f} "
                f"(need ≥ {min_pct:g}% of peak OI)"
            )
            print(f"  {price.symbol}: {log_tag} PUT skipped — {reason}")
            return self._s1_watch(price, s1, side, reason)

        call_wall = choose_s1_entry_wall(
            s1.legs_by_strike, price.ltp, "call", min_pct
        )
        put_wall = choose_s1_entry_wall(
            s1.legs_by_strike, price.ltp, "put", min_pct
        )
        if call_wall:
            apply_oi_wall(s1, call_wall, "call")
        else:
            s1.max_call_oi_strike = 0.0
            s1.max_call_oi = 0
            s1.max_call_token = ""
        if put_wall:
            apply_oi_wall(s1, put_wall, "put")
        else:
            s1.max_put_oi_strike = 0.0
            s1.max_put_oi = 0
            s1.max_put_token = ""

        too_far = proximity_skip_reason(price, s1, side, thresholds["proximity_pct"])
        if too_far:
            print(f"  {price.symbol}: {log_tag} — {too_far}")
            return self._s1_watch(price, s1, side, too_far)

        reference = "call" if side is SignalType.CALL_OI else "put"
        if not align_snapshot_to_reference_strike(s1, reference):
            reason = "CE/PE missing at reference strike"
            print(f"  {price.symbol}: {log_tag} skipped — {reason}")
            return self._s1_watch(price, s1, side, reason)

        self.client.add_oi_changes(s1)
        if pcr_band_strikes:
            wall = (
                s1.max_call_oi_strike
                if side is SignalType.CALL_OI
                else s1.max_put_oi_strike
            )
            self.client.add_band_oi_changes(
                s1, wall, n_below=pcr_band_strikes, n_above=pcr_band_strikes
            )
            flow = {**flow, "require_change_pcr": True}

        if side is SignalType.CALL_OI:
            rejected = call_oi_flow_rejection(s1, **flow)
        else:
            rejected = put_oi_flow_rejection(s1, **flow)
        if rejected:
            print(f"  {price.symbol}: {log_tag} {side.value} skipped — {rejected}")
            return self._s1_watch(price, s1, side, rejected)

        if s2_mode:
            size_skip = s2_size_skip_reason(
                s1,
                side,
                min_wall_contracts=self.config.oi.s2_min_wall_contracts,
                min_write_contracts=self.config.oi.s2_min_write_contracts,
            )
            if size_skip:
                print(f"  {price.symbol}: {log_tag} skipped — {size_skip}")
                return self._s1_watch(price, s1, side, size_skip)

        if side is SignalType.CALL_OI and call_wall and peak_call and call_wall[0] != peak_call[0]:
            print(
                f"  {price.symbol}: {log_tag} CALL using uncrossed ₹{call_wall[0]:.0f} "
                f"(peak ₹{peak_call[0]:.0f} already through price)"
            )
        if side is SignalType.PUT_OI and put_wall and peak_put and put_wall[0] != peak_put[0]:
            print(
                f"  {price.symbol}: {log_tag} PUT using uncrossed ₹{put_wall[0]:.0f} "
                f"(peak ₹{peak_put[0]:.0f} already through price)"
            )

        alert = evaluate_stock(price=price, oi=s1, **thresholds, **flow)
        if alert is None:
            return self._s1_watch(price, s1, side, "did not qualify")
        alert.signal = (
            SignalType.CALL_OI_S1 if side is SignalType.CALL_OI else SignalType.PUT_OI_S1
        )
        return alert

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

        if self.config.oi.skip_monthly_expiry:
            expiry_skip = expiry_entry_skip_reason()
            if expiry_skip:
                print(f"  {symbol}: ST skipped — {expiry_skip}")
                return make_supertrend_watch(
                    symbol=symbol,
                    ltp=ltp,
                    supertrend=st_value,
                    side=side,
                    oi=None,
                    skip_reason=expiry_skip,
                )

        blocked = no_short_skip_reason(
            symbol,
            self.config.no_short_symbols,
            is_short=side == "below",
        )
        if blocked:
            print(f"  {symbol}: ST short skipped — {blocked}")
            return make_supertrend_watch(
                symbol=symbol,
                ltp=ltp,
                supertrend=st_value,
                side=side,
                oi=None,
                skip_reason=blocked,
            )

        oi = self.client.get_oi_at_price(symbol, target_price=st_value, ltp=ltp)
        if not oi:
            reason = "OI unavailable"
            print(f"  {symbol}: near ST ₹{st_value:,.2f} — {reason}")
            return make_supertrend_watch(
                symbol=symbol,
                ltp=ltp,
                supertrend=st_value,
                side=side,
                oi=None,
                skip_reason=reason,
            )
        self.client.add_oi_changes(oi)

        if side == "below":
            rejected = call_oi_flow_rejection(oi, **self._oi_flow())
        else:
            rejected = put_oi_flow_rejection(oi, **self._oi_flow())
        if rejected:
            print(
                f"  {symbol}: near ST ₹{st_value:,.2f} ({side}, {distance:.2f}%) — {rejected}"
            )
            return make_supertrend_watch(
                symbol=symbol,
                ltp=ltp,
                supertrend=st_value,
                side=side,
                oi=oi,
                skip_reason=rejected,
            )

        return evaluate_supertrend_oi(
            symbol=symbol,
            ltp=ltp,
            supertrend=st_value,
            side=side,
            oi=oi,
            st_config=st_cfg,
            oi_config=self.config.oi,
        )

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
            expiry, lot = contract.expiry, contract.lot_size
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

    def _exit_s1_broken_walls(
        self,
        book: PaperBook,
        equity_prices: dict[str, float],
        fut_prices: dict[str, float],
        rsi_values: dict[str, float],
    ) -> list:
        """Close at 15:15 IST only if the *entry* strike is broken.

        A later peak Call/Put OI (e.g. short at 110, then max Call OI at 102
        with price 103) is not a break of the position we took.
        """
        events = []
        for position in list(book.positions):
            if not position.is_open or position.strike <= 0:
                continue
            price = equity_prices.get(position.symbol)
            if not price:
                continue

            oi = self.client.get_oi_at_price(
                position.symbol, target_price=position.strike, ltp=price
            )
            if not oi:
                continue
            self.client.add_oi_changes(oi)
            oi.max_call_oi_strike = position.strike
            oi.max_put_oi_strike = position.strike

            if position.direction == "LONG":
                broken = support_is_broken(oi, price)
                label = "support"
            else:
                broken = resistance_is_broken(oi, price)
                label = "resistance"

            if not broken:
                continue

            fill = fut_prices.get(position.symbol)
            if not fill:
                print(
                    f"  {position.symbol}: S1 {label} ₹{position.strike:.0f} broken — "
                    "no NSE fut LTP, not exiting on cash"
                )
                continue
            lots = position.lots_open
            event = book.close_on_broken_wall(
                position, fill, rsi_values.get(position.symbol)
            )
            events.append(event)
            print(
                f"  {position.symbol}: S1 {label} ₹{position.strike:.0f} broken — "
                f"exiting {lots} lot {position.direction} @ ₹{fill:,.2f} (fut)"
            )
        return events

    def _exit_s2_invalid_strikes(
        self,
        book: PaperBook,
        equity_prices: dict[str, float],
        fut_prices: dict[str, float],
        rsi_values: dict[str, float],
    ) -> list:
        """OI is the primary stop, confirmed on two consecutive scans:
        cash through the entry strike or writing gone at that strike.
        A single invalid print is stored on the position and ignored.
        Fill is 3rd-month NSE futures, never cash. The 3% futures stop
        in update() is a single-scan backup if the wall is still valid.
        """
        reasons = {
            "strike_through": ExitReason.STRIKE_THROUGH,
            "writing_gone": ExitReason.WRITING_GONE,
        }
        events = []
        for position in list(book.positions):
            if not position.is_open or position.strike <= 0:
                continue
            cash = equity_prices.get(position.symbol)
            if not cash:
                continue

            oi = self.client.get_oi_at_price(
                position.symbol, target_price=position.strike, ltp=cash
            )
            if oi:
                self.client.add_oi_changes(oi)
                call_d, put_d = oi.call_oi_change, oi.put_oi_change
            else:
                call_d = put_d = None

            why = s2_invalidation_reason(
                position.direction,
                position.strike,
                cash,
                call_oi_change=call_d,
                put_oi_change=put_d,
            )
            wall_valid = s2_wall_still_valid(
                position.direction,
                position.strike,
                cash,
                call_oi_change=call_d,
                put_oi_change=put_d,
            )
            previous = position.s2_invalid_pending
            position.s2_invalid_pending, confirmed = s2_confirm_invalidation(
                previous, why, wall_valid=wall_valid
            )
            if not confirmed:
                if why and not previous:
                    print(
                        f"  {position.symbol}: S2 {why} ₹{position.strike:.0f} — "
                        "first scan, holding for confirm"
                    )
                elif not why and previous:
                    print(
                        f"  {position.symbol}: S2 wall valid again ₹{position.strike:.0f} — "
                        f"cleared {previous}"
                    )
                continue

            fill = fut_prices.get(position.symbol)
            if not fill:
                print(
                    f"  {position.symbol}: S2 {why} ₹{position.strike:.0f} — "
                    "confirmed, no NSE fut LTP, not exiting on cash"
                )
                continue
            lots = position.lots_open
            event = book.close_remaining(
                position,
                fill,
                reasons[why],
                rsi_values.get(position.symbol),
            )
            events.append(event)
            print(
                f"  {position.symbol}: S2 {why} ₹{position.strike:.0f} — "
                f"confirmed, exiting {lots} lot {position.direction} "
                f"@ ₹{fill:,.2f} (fut)"
            )
        return events

    def _run_one_paper_book(
        self,
        book: PaperBook,
        alerts: list[ScanAlert],
        prices: dict[str, float],
        rsi_values: dict[str, float],
    ) -> None:
        self._align_open_futures_expiry(book)
        self._apply_futures_expiry(alerts)

        fut_prices = self._futures_paper_prices(book, alerts)
        events = self._restate_cash_entries(book)
        events += book.drop_void_positions(
            skip_monthly_expiry=self.config.oi.skip_monthly_expiry
        )

        paper_alerts: list[ScanAlert] = []
        for alert in alerts:
            if alert.skip_reason:
                continue
            if alert.symbol not in fut_prices:
                print(
                    f"  {alert.symbol}: not opening paper — "
                    "no NSE 3rd-month futures LTP (scan used NSE cash + current-month OI)"
                )
                continue
            alert.ltp = fut_prices[alert.symbol]
            paper_alerts.append(alert)

        # S2: confirmed OI invalidation is the primary stop (two consecutive
        # scans). Run it before the 3% futures cap so a dead wall is booked
        # as strike_through / writing_gone, not stop_loss.
        if book is self.s2_book:
            events += self._exit_s2_invalid_strikes(
                book, prices, fut_prices, rsi_values
            )
        events += book.update(fut_prices, rsi_values=rsi_values)
        if book is self.s1_book and is_s1_wall_exit_slot():
            events += self._exit_s1_broken_walls(
                book, prices, fut_prices, rsi_values
            )
        if not is_close_pnl_slot():
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
        rsi_alerts = [
            a
            for a in alerts
            if a.signal in (SignalType.CALL_OI, SignalType.PUT_OI) and not a.skip_reason
        ]
        st_alerts = [
            a
            for a in alerts
            if a.signal in (SignalType.ST_BEARISH, SignalType.ST_BULLISH)
            and not a.skip_reason
        ]

        if self.book:
            self._run_one_paper_book(self.book, rsi_alerts, prices, rsi_values)
        if self.s1_book:
            s1_alerts = [
                a
                for a in alerts
                if a.signal in (SignalType.CALL_OI_S1, SignalType.PUT_OI_S1)
                and not a.skip_reason
            ]
            self._run_one_paper_book(self.s1_book, s1_alerts, prices, rsi_values)
        if self.s2_book:
            s2_alerts = [
                a
                for a in alerts
                if a.signal in (SignalType.CALL_OI_S2, SignalType.PUT_OI_S2)
                and not a.skip_reason
            ]
            self._run_one_paper_book(self.s2_book, s2_alerts, prices, rsi_values)
        if self.st_book:
            self._run_one_paper_book(self.st_book, st_alerts, prices, rsi_values)

    def run_once(self) -> list[ScanAlert]:
        started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        symbols = self.symbols()
        print(f"\nScan started at {started} over {len(symbols)} stocks (live Angel One data)")
        print(f"  {oi_scan_reason()}")

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
            oi = self.client.get_oi_snapshot(price.symbol, ltp=price.ltp)
            s1_oi = copy_oi_snapshot(oi) if oi else None
            alert = self._check_candidate(price, oi)
            if alert:
                alerts.append(alert)
            if self.s1_book:
                s1_alert = self._check_scenario1_candidate(price, s1_oi)
                if s1_alert:
                    alerts.append(s1_alert)
            if self.s2_book:
                s2_src = self._check_scenario1_candidate(
                    price,
                    copy_oi_snapshot(oi) if oi else None,
                    proximity_pct=self.config.oi.s2_proximity_pct,
                    pcr_band_strikes=self.config.oi.s2_pcr_strikes,
                    log_tag="S2",
                    s2_mode=True,
                )
                if s2_src:
                    alerts.append(retag_s1_alert_as_s2(s2_src))

        rsi_batch = [
            a
            for a in alerts
            if a.signal
            in (
                SignalType.CALL_OI,
                SignalType.PUT_OI,
                SignalType.CALL_OI_S1,
                SignalType.PUT_OI_S1,
                SignalType.CALL_OI_S2,
                SignalType.PUT_OI_S2,
            )
        ]
        if is_close_pnl_slot():
            print("  15:45 close — skipping RSI/ST signal Telegram; sending closing P&L")
        else:
            self._emit_telegram(rsi_batch, "RSI+OI")

        if self.config.supertrend.enabled:
            st_cfg = self.config.supertrend
            print(
                f"\nSupertrend scan ({st_cfg.atr_period}, {st_cfg.multiplier}) — "
                f"proximity {st_cfg.proximity_pct}%"
            )
            try:
                st_map = fetch_supertrends(
                    symbols,
                    prices,
                    atr_period=st_cfg.atr_period,
                    multiplier=st_cfg.multiplier,
                )
            except Exception as exc:
                print(f"  Supertrend Yahoo download failed: {exc}")
                st_map = {}
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

        st_batch = [
            a
            for a in alerts
            if a.signal in (SignalType.ST_BEARISH, SignalType.ST_BULLISH)
        ]
        if not is_close_pnl_slot():
            self._emit_telegram(st_batch, "Supertrend+OI")

        if not alerts:
            print("\nNo alerts this scan.")

        self._run_paper_trading(alerts, prices, rsi_values)
        return alerts
