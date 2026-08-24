from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from src.config import PaperTradingConfig, SignalType
from src.oi_analyzer import ScanAlert
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
    ):
        self.config = config
        self.path = Path(path or config.ledger_path)
        self.journal = journal
        self.positions: list[Position] = []
        self.realised_pnl: float = 0.0
        self.closed_count: int = 0
        self.day_date: str = str(date.today())
        self.day_realised_pnl: float = 0.0
        self._pending_rows: list[dict] = []
        self._load()

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #

    def _roll_day(self, today: date | None = None) -> None:
        """Reset the day bucket when the calendar date changes."""
        today_s = str(today or date.today())
        if self.day_date != today_s:
            self.day_date = today_s
            self.day_realised_pnl = 0.0

    def _load(self) -> None:
        if not self.path.exists():
            return

        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.positions = [Position.from_dict(p) for p in raw.get("positions", [])]
        self.realised_pnl = float(raw.get("realised_pnl", 0.0))
        self.closed_count = int(raw.get("closed_count", 0))
        self.day_date = str(raw.get("day_date") or date.today())
        self.day_realised_pnl = float(raw.get("day_realised_pnl", 0.0))
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

        return TradeEvent(
            symbol=position.symbol,
            kind=reason.value,
            detail=(
                f"{position.direction} {lots} lot(s) @ ₹{price:,.2f} "
                f"(entry ₹{position.entry_price:,.2f})"
            ),
            pnl=pnl,
        )

    def _stop_pct(self, position: Position) -> float:
        """Full stop until the first lot is booked; then tighten from entry."""
        if position.lots_open < position.lots_total:
            return self.config.second_lot_stop_pct
        return self.config.stop_loss_pct

    def _scale_targets(self) -> list[tuple[float, ExitReason]]:
        """One lot per target; the last target closes whatever is still open."""
        targets = [
            (self.config.first_target_pct, ExitReason.FIRST_TARGET),
            (self.config.second_target_pct, ExitReason.SECOND_TARGET),
        ]
        if self.config.third_target_pct is not None:
            targets.append((self.config.third_target_pct, ExitReason.THIRD_TARGET))
        return targets

    def _apply_exits(
        self, position: Position, price: float, today: date, rsi: float | None
    ) -> list[TradeEvent]:
        events: list[TradeEvent] = []
        move = position.move_pct(price)

        # Stop first: on a 30-minute snapshot we cannot know the intrabar order,
        # so assume the adverse level was reached before any target.
        stop_pct = self._stop_pct(position)
        if move <= -stop_pct + TRIGGER_TOLERANCE:
            stop_price = position.price_at_move(-stop_pct)
            events.append(
                self._close_lots(
                    position, position.lots_open, stop_price, ExitReason.STOP_LOSS, rsi
                )
            )
            return events

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
    ) -> list[TradeEvent]:
        """Mark open positions to market and run the exit rules."""
        today = today or date.today()
        rsi_values = rsi_values or {}
        events: list[TradeEvent] = []

        for position in list(self.positions):
            price = prices.get(position.symbol)
            if not price or not position.is_open:
                continue
            events.extend(
                self._apply_exits(position, price, today, rsi_values.get(position.symbol))
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

    def close_on_broken_wall(
        self,
        position: Position,
        price: float,
        exit_rsi: float | None = None,
    ) -> TradeEvent:
        """Exit remaining lots after the entry support/resistance wall is broken."""
        event = self._close_lots(
            position, position.lots_open, price, ExitReason.WALL_BROKEN, exit_rsi
        )
        self.positions = [item for item in self.positions if item.is_open]
        return event

    def flush_journal(self) -> int:
        """Write any closed trades out to the journal."""
        rows, self._pending_rows = self._pending_rows, []
        if rows and self.journal:
            self.journal.append(rows)
        return len(rows)

    # ------------------------------------------------------------------ #
    # Entries
    # ------------------------------------------------------------------ #

    def _direction_for(self, alert: ScanAlert) -> Direction:
        # RSI Call OI / ST bearish → short; RSI Put OI / ST bullish → long.
        if alert.signal in (SignalType.CALL_OI, SignalType.ST_BEARISH, SignalType.CALL_OI_S1):
            return Direction.SHORT
        return Direction.LONG

    def open_from_alerts(self, alerts: list[ScanAlert]) -> list[TradeEvent]:
        events: list[TradeEvent] = []
        held = {p.symbol for p in self.positions if p.is_open}

        for alert in alerts:
            if alert.symbol in held:
                continue
            if alert.skip_reason:
                continue
            if alert.lot_size <= 0 or alert.ltp <= 0:
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

            direction = self._direction_for(alert)
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
                        f"fut {alert.expiry})"
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
