from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from src.config import PaperTradingConfig, SignalType
from src.data.option_expiry import opened_on_stock_monthly_expiry
from src.oi_analyzer import ScanAlert, no_short_skip_reason
from src.paper_trading.journal import TradeJournal, build_row, build_summary_row
from src.paper_trading.models import (
    ClosedLeg,
    Direction,
    ExitReason,
    Position,
    TradeEvent,
    now_stamp,
)


# A price landing exactly on a target can compute as 5.999...% instead of 6%,
# so comparisons carry a tolerance rather than missing the trigger.
TRIGGER_TOLERANCE = 1e-9


class PaperBook:
    """A paper futures book: scale out one lot at each target, last target takes the rest.

    The scanner only sees a price every 30 minutes, so targets and stops are
    booked at their exact trigger levels rather than the observed price. That
    mirrors resting limit and stop orders, but it does mean gaps through a stop
    are modelled optimistically.
    """

    def __init__(
        self,
        config: PaperTradingConfig,
        path: str | Path | None = None,
        journal: TradeJournal | None = None,
        no_short_symbols: list[str] | None = None,
    ):
        self.config = config
        self.path = Path(path or config.ledger_path)
        self.journal = journal
        self.no_short_symbols = {s.upper() for s in (no_short_symbols or [])}
        self.positions: list[Position] = []
        self.realised_pnl: float = 0.0
        self.closed_count: int = 0
        self.day_date: str = str(date.today())
        self.day_realised_pnl: float = 0.0
        self.blocked_reentry: set[str] = set()
        self.session_dates: list[str] = []
        self.closed_results: list[dict] = []
        self.qualify_skips: dict[str, int] = {}
        self._pending_rows: list[dict] = []
        self._load()

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #

    def _note_session(self, day_s: str) -> None:
        if day_s and day_s not in self.session_dates:
            self.session_dates.append(day_s)
            self.session_dates.sort()

    def _roll_day(self, today: date | None = None) -> None:
        """Reset the day bucket when the calendar date changes."""
        today_s = str(today or date.today())
        if self.day_date != today_s:
            self.day_date = today_s
            self.day_realised_pnl = 0.0
            self.blocked_reentry = set()
        self._note_session(today_s)

    def _load(self) -> None:
        if not self.path.exists():
            return

        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.positions = [Position.from_dict(p) for p in raw.get("positions", [])]
        self.realised_pnl = float(raw.get("realised_pnl", 0.0))
        self.closed_count = int(raw.get("closed_count", 0))
        self.day_date = str(raw.get("day_date") or date.today())
        self.day_realised_pnl = float(raw.get("day_realised_pnl", 0.0))
        self.blocked_reentry = {
            str(symbol) for symbol in raw.get("blocked_reentry") or []
        }
        self.session_dates = [str(d) for d in raw.get("session_dates") or []]
        self.closed_results = [
            row
            for row in (raw.get("closed_results") or [])
            if isinstance(row, dict) and row.get("symbol")
        ]
        self.qualify_skips = {
            str(symbol): int(left)
            for symbol, left in (raw.get("qualify_skips") or {}).items()
            if int(left) > 0
        }
        self._roll_day()

    def save(self) -> None:
        self._roll_day()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "capital": self.config.capital,
            "realised_pnl": round(self.realised_pnl, 2),
            "closed_count": self.closed_count,
            "day_date": self.day_date,
            "day_realised_pnl": round(self.day_realised_pnl, 2),
            "updated_at": now_stamp(),
            "blocked_reentry": sorted(self.blocked_reentry),
            "session_dates": self.session_dates,
            "closed_results": self.closed_results,
            "qualify_skips": {
                symbol: left
                for symbol, left in sorted(self.qualify_skips.items())
                if left > 0
            },
            "positions": [p.to_dict() for p in self.positions],
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------ #
    # Capital
    # ------------------------------------------------------------------ #

    @property
    def margin_blocked(self) -> float:
        return sum(p.margin_blocked for p in self.positions if p.is_open)

    @property
    def free_capital(self) -> float:
        return self.config.capital + self.realised_pnl - self.margin_blocked

    def _margin_for(self, price: float, lot_size: int, lots: int) -> float:
        notional = price * lot_size * lots
        return notional * self.config.margin_pct / 100

    # ------------------------------------------------------------------ #
    # Exits
    # ------------------------------------------------------------------ #

    def _close_lots(
        self,
        position: Position,
        lots: int,
        price: float,
        reason: ExitReason,
        exit_rsi: float | None = None,
    ) -> TradeEvent:
        pnl = position.pnl_for(lots, price)
        exit_time = now_stamp()
        position.closed_legs.append(
            ClosedLeg(
                lots=lots,
                exit_price=round(price, 2),
                exit_time=exit_time,
                reason=reason.value,
                pnl=round(pnl, 2),
            )
        )

        # Release the margin backing the lots being closed.
        released = position.margin_blocked * lots / max(position.lots_open, 1)
        position.margin_blocked -= released
        position.lots_open -= lots
        self.realised_pnl += pnl
        self._roll_day()
        self.day_realised_pnl += pnl

        margin_per_lot = (
            released / lots
            if lots
            else position.margin_blocked / max(position.lots_open, 1)
        )
        self._pending_rows.append(
            build_row(
                symbol=position.symbol,
                direction=position.direction,
                entry_time=position.entry_time,
                entry_price=position.entry_price,
                entry_rsi=position.rsi_at_entry,
                exit_time=exit_time,
                exit_price=price,
                exit_rsi=exit_rsi,
                lots=lots,
                margin_per_lot=margin_per_lot,
                pnl=pnl,
                reason=reason.value,
            )
        )

        if not position.is_open:
            self.closed_count += 1
            position.margin_blocked = 0.0
            if self.config.block_same_day_reentry and reason in (
                ExitReason.STOP_LOSS,
                ExitReason.STRIKE_THROUGH,
                ExitReason.WRITING_GONE,
                ExitReason.WALL_BROKEN,
            ):
                self.blocked_reentry.add(position.symbol)
            self._record_closed_trade(position)

        return TradeEvent(
            symbol=position.symbol,
            kind=reason.value,
            detail=(
                f"{position.direction} {lots} lot(s) @ ₹{price:,.2f} "
                f"(entry ₹{position.entry_price:,.2f})"
            ),
            pnl=pnl,
        )

    def _loss_skip_enabled(self) -> bool:
        return (
            self.config.loss_streak_count > 0
            and self.config.loss_streak_sessions > 0
            and self.config.skip_qualifies_after_streak > 0
        )

    def _session_span(self, start: str, end: str) -> int:
        self._note_session(start)
        self._note_session(end)
        first = self.session_dates.index(start)
        last = self.session_dates.index(end)
        return abs(last - first) + 1

    def _trim_closed_results(self) -> None:
        keep = 12
        by_symbol: dict[str, list[dict]] = {}
        for row in self.closed_results:
            by_symbol.setdefault(str(row["symbol"]), []).append(row)
        trimmed: list[dict] = []
        for rows in by_symbol.values():
            trimmed.extend(rows[-keep:])
        self.closed_results = trimmed

    def _record_closed_trade(self, position: Position) -> None:
        if not self._loss_skip_enabled():
            return
        exit_date = ""
        if position.closed_legs:
            exit_date = str(position.closed_legs[-1].exit_time)[:10]
        exit_date = exit_date or self.day_date
        self._note_session(exit_date)
        net = round(sum(leg.pnl for leg in position.closed_legs), 2)
        self.closed_results.append(
            {
                "symbol": position.symbol,
                "exit_date": exit_date,
                "pnl": net,
            }
        )
        self._trim_closed_results()
        self._maybe_arm_qualify_skip(position.symbol)

    def _maybe_arm_qualify_skip(self, symbol: str) -> None:
        need = self.config.loss_streak_count
        window = self.config.loss_streak_sessions
        rows = [row for row in self.closed_results if row["symbol"] == symbol]
        if len(rows) < need:
            return
        streak = rows[-need:]
        if any(float(row["pnl"]) >= 0 for row in streak):
            return
        span = self._session_span(str(streak[0]["exit_date"]), str(streak[-1]["exit_date"]))
        if span > window:
            return
        self.qualify_skips[symbol] = self.config.skip_qualifies_after_streak

    def _consume_qualify_skip(self, symbol: str) -> str | None:
        left = self.qualify_skips.get(symbol, 0)
        if left <= 0:
            return None
        total = self.config.skip_qualifies_after_streak
        used = total - left + 1
        left -= 1
        if left <= 0:
            self.qualify_skips.pop(symbol, None)
        else:
            self.qualify_skips[symbol] = left
        return (
            f"{self.config.loss_streak_count} consecutive losses in "
            f"{self.config.loss_streak_sessions} sessions — sitting out "
            f"qualify {used}/{total}"
        )

    def _stop_pct(self, position: Position) -> float:
        """Full stop until the first lot is booked; then tighten from entry."""
        if position.lots_open < position.lots_total:
            return self.config.second_lot_stop_pct
        return self.config.stop_loss_pct

    def _uses_smma_targets(self) -> bool:
        return self.config.smma_fast is not None and self.config.smma_slow is not None

    def _scale_targets(self) -> list[tuple[float, ExitReason]]:
        """One lot per target; the last target closes whatever is still open."""
        targets = [
            (self.config.first_target_pct, ExitReason.FIRST_TARGET),
            (self.config.second_target_pct, ExitReason.SECOND_TARGET),
        ]
        if self.config.third_target_pct is not None:
            targets.append((self.config.third_target_pct, ExitReason.THIRD_TARGET))
        return targets

    @staticmethod
    def _level_is_profit(position: Position, level: float) -> bool:
        """SMMA is a take-profit only when it sits on the winning side of entry."""
        if position.direction == Direction.LONG:
            return level > position.entry_price + TRIGGER_TOLERANCE
        return level < position.entry_price - TRIGGER_TOLERANCE

    @staticmethod
    def _level_reached(position: Position, price: float, level: float) -> bool:
        if position.direction == Direction.LONG:
            return price >= level - TRIGGER_TOLERANCE
        return price <= level + TRIGGER_TOLERANCE

    def _rsi_second_lot_hit(self, position: Position, rsi: float | None) -> bool:
        if rsi is None:
            return False
        if position.direction == Direction.SHORT:
            threshold = self.config.second_lot_rsi_short
            return threshold is not None and rsi <= threshold + TRIGGER_TOLERANCE
        threshold = self.config.second_lot_rsi_long
        return threshold is not None and rsi >= threshold - TRIGGER_TOLERANCE

    def _apply_smma_exits(
        self,
        position: Position,
        price: float,
        rsi: float | None,
        smma_levels: tuple[float | None, float | None] | None,
    ) -> list[TradeEvent]:
        """1 lot at SMMA 21; remaining at SMMA 50, or RSI 30/70 if that prints first."""
        events: list[TradeEvent] = []
        fast, slow = smma_levels if smma_levels is not None else (None, None)

        if position.lots_open == position.lots_total:
            if (
                fast is not None
                and self._level_is_profit(position, fast)
                and self._level_reached(position, price, fast)
            ):
                events.append(
                    self._close_lots(position, 1, fast, ExitReason.FIRST_TARGET, rsi)
                )
            if position.lots_open == position.lots_total:
                return events

        if not position.is_open:
            return events

        if (
            slow is not None
            and self._level_is_profit(position, slow)
            and self._level_reached(position, price, slow)
        ):
            events.append(
                self._close_lots(
                    position,
                    position.lots_open,
                    slow,
                    ExitReason.SECOND_TARGET,
                    rsi,
                )
            )
            return events

        if self._rsi_second_lot_hit(position, rsi):
            events.append(
                self._close_lots(
                    position,
                    position.lots_open,
                    price,
                    ExitReason.RSI_TARGET,
                    rsi,
                )
            )
        return events

    def _candle_stop_fill(self, position: Position, price: float) -> float | None:
        """Both lots share this cash-bar stop when it was stored at entry."""
        stop = position.stop_price
        if stop is None or stop <= 0:
            return None
        if position.direction == Direction.SHORT:
            if price >= stop - TRIGGER_TOLERANCE:
                return stop
            return None
        if price <= stop + TRIGGER_TOLERANCE:
            return stop
        return None

    def _apply_exits(
        self,
        position: Position,
        price: float,
        today: date,
        rsi: float | None,
        smma_levels: tuple[float | None, float | None] | None = None,
        *,
        skip_candle_stop: bool = False,
        stop_prices: dict[str, float] | None = None,
    ) -> list[TradeEvent]:
        events: list[TradeEvent] = []
        move = position.move_pct(price)

        # Stop first: on a 30-minute snapshot we cannot know the intrabar order,
        # so assume the adverse level was reached before any target.
        if skip_candle_stop:
            candle_stop = None
        elif stop_prices is not None:
            cash = stop_prices.get(position.symbol)
            candle_stop = (
                self._candle_stop_fill(position, cash) if cash else None
            )
        else:
            candle_stop = self._candle_stop_fill(position, price)
        if candle_stop is not None:
            events.append(
                self._close_lots(
                    position,
                    position.lots_open,
                    candle_stop,
                    ExitReason.STOP_LOSS,
                    rsi,
                )
            )
            return events

        if position.stop_price is None:
            stop_pct = self._stop_pct(position)
            if move <= -stop_pct + TRIGGER_TOLERANCE:
                stop_price = position.price_at_move(-stop_pct)
                events.append(
                    self._close_lots(
                        position, position.lots_open, stop_price, ExitReason.STOP_LOSS, rsi
                    )
                )
                return events

        if self._uses_smma_targets():
            events.extend(self._apply_smma_exits(position, price, rsi, smma_levels))
        else:
            targets = self._scale_targets()
            for index, (pct, reason) in enumerate(targets):
                if not position.is_open:
                    break
                if move < pct - TRIGGER_TOLERANCE:
                    break
                already_closed = position.lots_total - position.lots_open
                if already_closed != index:
                    continue
                lots = position.lots_open if index == len(targets) - 1 else 1
                events.append(
                    self._close_lots(
                        position,
                        lots,
                        position.price_at_move(pct),
                        reason,
                        rsi,
                    )
                )

        if position.is_open and position.expiry and str(today) >= position.expiry:
            events.append(
                self._close_lots(position, position.lots_open, price, ExitReason.EXPIRY, rsi)
            )

        return events

    def update(
        self,
        prices: dict[str, float],
        today: date | None = None,
        rsi_values: dict[str, float] | None = None,
        smma_levels: dict[str, tuple[float | None, float | None]] | None = None,
        *,
        skip_candle_stop: bool = False,
        stop_prices: dict[str, float] | None = None,
    ) -> list[TradeEvent]:
        """Mark open positions to market and run the exit rules."""
        today = today or date.today()
        rsi_values = rsi_values or {}
        smma_levels = smma_levels or {}
        events: list[TradeEvent] = []

        for position in list(self.positions):
            price = prices.get(position.symbol)
            if not price or not position.is_open:
                continue
            events.extend(
                self._apply_exits(
                    position,
                    price,
                    today,
                    rsi_values.get(position.symbol),
                    smma_levels.get(position.symbol),
                    skip_candle_stop=skip_candle_stop,
                    stop_prices=stop_prices,
                )
            )

        self.positions = [p for p in self.positions if p.is_open]
        return events

    def rebase_entries_to_futures(self, prices: dict[str, float]) -> list[TradeEvent]:
        """Move cash-priced paper fills onto the 3rd-month future.

        `prices` must be the futures print on the original entry date — not
        today's LTP — so open P&L is restated instead of zeroed.
        """
        events: list[TradeEvent] = []
        for position in self.positions:
            if not position.is_open or position.priced_on == "futures":
                continue
            price = prices.get(position.symbol)
            if not price:
                continue
            old = position.entry_price
            position.entry_price = round(price, 2)
            position.priced_on = "futures"
            position.margin_blocked = self._margin_for(
                position.entry_price, position.lot_size, position.lots_open
            )
            events.append(
                TradeEvent(
                    symbol=position.symbol,
                    kind="rebase",
                    detail=(
                        f"{position.direction} entry ₹{old:,.2f} (cash) → "
                        f"₹{position.entry_price:,.2f} (3rd-month fut)"
                    ),
                )
            )
        return events

    def close_remaining(
        self,
        position: Position,
        price: float,
        reason: ExitReason,
        exit_rsi: float | None = None,
    ) -> TradeEvent:
        """Exit remaining lots at the given futures price for a named reason."""
        event = self._close_lots(
            position, position.lots_open, price, reason, exit_rsi
        )
        self.positions = [item for item in self.positions if item.is_open]
        return event

    def close_on_broken_wall(
        self,
        position: Position,
        price: float,
        exit_rsi: float | None = None,
    ) -> TradeEvent:
        """Exit remaining lots after the entry support/resistance wall is broken."""
        return self.close_remaining(
            position, price, ExitReason.WALL_BROKEN, exit_rsi
        )

    def flush_journal(self) -> int:
        """Write any closed trades out to the journal."""
        rows, self._pending_rows = self._pending_rows, []
        if rows and self.journal:
            self.journal.append(rows)
        return len(rows)

    # ------------------------------------------------------------------ #
    # Entries
    # ------------------------------------------------------------------ #

    def drop_void_positions(self, *, skip_monthly_expiry: bool = True) -> list[TradeEvent]:
        """Remove lab shorts and expiry-day opens without booking P&L."""
        events: list[TradeEvent] = []
        kept: list[Position] = []
        for position in self.positions:
            if not position.is_open:
                kept.append(position)
                continue
            blocked = no_short_skip_reason(
                position.symbol,
                self.no_short_symbols,
                is_short=position.direction == Direction.SHORT,
            )
            if blocked:
                events.append(
                    TradeEvent(
                        symbol=position.symbol,
                        kind="removed",
                        detail=blocked,
                    )
                )
                continue
            if skip_monthly_expiry and opened_on_stock_monthly_expiry(
                position.entry_time
            ):
                events.append(
                    TradeEvent(
                        symbol=position.symbol,
                        kind="removed",
                        detail="opened on stock monthly expiry",
                    )
                )
                continue
            kept.append(position)
        self.positions = kept
        return events

    def _direction_for(self, alert: ScanAlert) -> Direction:
        # RSI Call OI / ST bearish → short; RSI Put OI / ST bullish → long.
        if alert.signal in (
            SignalType.CALL_OI,
            SignalType.ST_BEARISH,
            SignalType.CALL_OI_S1,
            SignalType.CALL_OI_S2,
            SignalType.RSI_CANDLE_SHORT,
        ):
            return Direction.SHORT
        return Direction.LONG

    def open_from_alerts(self, alerts: list[ScanAlert]) -> list[TradeEvent]:
        events: list[TradeEvent] = []
        held = {p.symbol for p in self.positions if p.is_open}

        for alert in alerts:
            if alert.symbol in held:
                continue
            if (
                self.config.block_same_day_reentry
                and alert.symbol in self.blocked_reentry
            ):
                events.append(
                    TradeEvent(
                        symbol=alert.symbol,
                        kind="skipped",
                        detail="already stopped/invalidated today — no S2 re-entry",
                    )
                )
                continue
            if alert.skip_reason:
                continue
            if alert.lot_size <= 0 or alert.ltp <= 0:
                continue

            direction = self._direction_for(alert)
            blocked = no_short_skip_reason(
                alert.symbol,
                self.no_short_symbols,
                is_short=direction is Direction.SHORT,
            )
            if blocked:
                events.append(
                    TradeEvent(symbol=alert.symbol, kind="skipped", detail=blocked)
                )
                continue

            sit_out = self._consume_qualify_skip(alert.symbol)
            if sit_out:
                events.append(
                    TradeEvent(symbol=alert.symbol, kind="skipped", detail=sit_out)
                )
                continue

            lots = self.config.lots_per_trade
            margin = self._margin_for(alert.ltp, alert.lot_size, lots)
            if margin > self.free_capital:
                events.append(
                    TradeEvent(
                        symbol=alert.symbol,
                        kind="skipped",
                        detail=f"needs ₹{margin:,.0f} margin, ₹{self.free_capital:,.0f} free",
                    )
                )
                continue

            position = Position(
                symbol=alert.symbol,
                direction=direction.value,
                entry_price=alert.ltp,
                entry_time=now_stamp(),
                lot_size=alert.lot_size,
                lots_open=lots,
                lots_total=lots,
                expiry=alert.expiry,
                rsi_at_entry=alert.rsi,
                strike=alert.oi_strike,
                margin_blocked=margin,
                priced_on="futures",
                stop_price=alert.stop_price,
            )
            self.positions.append(position)
            held.add(alert.symbol)

            events.append(
                TradeEvent(
                    symbol=alert.symbol,
                    kind="entry",
                    detail=(
                        f"{direction.value} {lots} lot(s) x {alert.lot_size} @ "
                        f"₹{alert.ltp:,.2f} ({alert.signal.value}, strike ₹{alert.oi_strike:,.0f}, "
                        f"fut {alert.expiry}"
                        + (
                            f", stop ₹{alert.stop_price:,.2f}"
                            if alert.stop_price
                            else ""
                        )
                        + ")"
                    ),
                )
            )

        return events

    # ------------------------------------------------------------------ #
    # Reporting
    # ------------------------------------------------------------------ #

    def unrealised(self, prices: dict[str, float]) -> float:
        return sum(
            p.unrealised(prices[p.symbol])
            for p in self.positions
            if p.is_open and p.symbol in prices
        )

    def day_pnl(self, prices: dict[str, float]) -> float:
        """Exits booked today plus current mark-to-market on open lots."""
        self._roll_day()
        return self.day_realised_pnl + self.unrealised(prices)

    def portfolio_summary_row(self, prices: dict[str, float]) -> dict:
        """Current RSI+OI book state for the half-hour Google Sheets log."""
        open_pnl = self.unrealised(prices)
        return build_summary_row(
            positions=len([position for position in self.positions if position.is_open]),
            capital_used=self.margin_blocked,
            realised_pnl=self.realised_pnl,
            unrealised_pnl=open_pnl,
        )

    def summary(self, prices: dict[str, float]) -> str:
        open_pnl = self.unrealised(prices)
        total = self.realised_pnl + open_pnl
        equity = self.config.capital + total
        day = self.day_pnl(prices)

        lines = [
            "Paper book",
            f"  Open positions   {len(self.positions)}",
            f"  Day P&L          ₹{day:,.0f}  (realised today ₹{self.day_realised_pnl:,.0f} + open ₹{open_pnl:,.0f})",
            f"  Realised P&L     ₹{self.realised_pnl:,.0f} over {self.closed_count} closed trade(s)",
            f"  Unrealised P&L   ₹{open_pnl:,.0f}",
            f"  Book value       ₹{equity:,.0f} ({total / self.config.capital * 100:+.2f}%)",
            f"  Free capital     ₹{self.free_capital:,.0f}",
        ]
        return "\n".join(lines)

    def telegram_report(
        self,
        prices: dict[str, float],
        events: list[TradeEvent] | None = None,
        *,
        closing: bool = False,
    ) -> str:
        """HTML caption for the positions image (parse_mode=HTML)."""
        from src.paper_trading.dashboard import format_pnl_dashboard

        self._roll_day()
        return format_pnl_dashboard(
            capital=self.config.capital,
            free_capital=self.free_capital,
            realised_pnl=self.realised_pnl,
            day_realised_pnl=self.day_realised_pnl,
            closed_count=self.closed_count,
            positions=self.positions,
            prices=prices,
            events=events,
            book_name=self.config.name,
            closing=closing,
        )

    def telegram_dashboard_image(
        self,
        prices: dict[str, float],
        events: list[TradeEvent] | None = None,
        *,
        closing: bool = False,
    ) -> bytes:
        """Angel One–style positions board as a PNG for Telegram."""
        from src.paper_trading.dashboard import render_positions_image

        self._roll_day()
        return render_positions_image(
            capital=self.config.capital,
            free_capital=self.free_capital,
            realised_pnl=self.realised_pnl,
            day_realised_pnl=self.day_realised_pnl,
            closed_count=self.closed_count,
            positions=self.positions,
            prices=prices,
            events=events,
            book_name=self.config.name,
            closing=closing,
        )
