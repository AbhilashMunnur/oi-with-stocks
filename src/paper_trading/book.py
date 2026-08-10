from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from src.config import PaperTradingConfig, SignalType
from src.oi_analyzer import ScanAlert
from src.paper_trading.models import (
    ClosedLeg,
    Direction,
    ExitReason,
    Position,
    TradeEvent,
    now_stamp,
)


class PaperBook:
    """A paper futures book: two lots per trade, scaled out at two targets.

    The scanner only sees a price every 30 minutes, so targets and stops are
    booked at their exact trigger levels rather than the observed price. That
    mirrors resting limit and stop orders, but it does mean gaps through a stop
    are modelled optimistically.
    """

    def __init__(self, config: PaperTradingConfig, path: str | Path | None = None):
        self.config = config
        self.path = Path(path or config.ledger_path)
        self.positions: list[Position] = []
        self.realised_pnl: float = 0.0
        self.closed_count: int = 0
        self._load()

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #

    def _load(self) -> None:
        if not self.path.exists():
            return

        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.positions = [Position.from_dict(p) for p in raw.get("positions", [])]
        self.realised_pnl = float(raw.get("realised_pnl", 0.0))
        self.closed_count = int(raw.get("closed_count", 0))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "capital": self.config.capital,
            "realised_pnl": round(self.realised_pnl, 2),
            "closed_count": self.closed_count,
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
        self, position: Position, lots: int, price: float, reason: ExitReason
    ) -> TradeEvent:
        pnl = position.pnl_for(lots, price)
        position.closed_legs.append(
            ClosedLeg(
                lots=lots,
                exit_price=round(price, 2),
                exit_time=now_stamp(),
                reason=reason.value,
                pnl=round(pnl, 2),
            )
        )

        # Release the margin backing the lots being closed.
        released = position.margin_blocked * lots / max(position.lots_open, 1)
        position.margin_blocked -= released
        position.lots_open -= lots
        self.realised_pnl += pnl

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

    def _apply_exits(self, position: Position, price: float, today: date) -> list[TradeEvent]:
        events: list[TradeEvent] = []
        move = position.move_pct(price)

        # Stop first: on a 30-minute snapshot we cannot know the intrabar order,
        # so assume the adverse level was reached before any target.
        if move <= -self.config.stop_loss_pct:
            stop_price = position.price_at_move(-self.config.stop_loss_pct)
            events.append(
                self._close_lots(position, position.lots_open, stop_price, ExitReason.STOP_LOSS)
            )
            return events

        first_target = self.config.first_target_pct
        second_target = self.config.second_target_pct

        if move >= first_target and position.lots_open == position.lots_total:
            events.append(
                self._close_lots(
                    position, 1, position.price_at_move(first_target), ExitReason.FIRST_TARGET
                )
            )

        if move >= second_target and position.is_open:
            events.append(
                self._close_lots(
                    position,
                    position.lots_open,
                    position.price_at_move(second_target),
                    ExitReason.SECOND_TARGET,
                )
            )

        if position.is_open and position.expiry and str(today) >= position.expiry:
            events.append(
                self._close_lots(position, position.lots_open, price, ExitReason.EXPIRY)
            )

        return events

    def update(self, prices: dict[str, float], today: date | None = None) -> list[TradeEvent]:
        """Mark open positions to market and run the exit rules."""
        today = today or date.today()
        events: list[TradeEvent] = []

        for position in list(self.positions):
            price = prices.get(position.symbol)
            if not price or not position.is_open:
                continue
            events.extend(self._apply_exits(position, price, today))

        self.positions = [p for p in self.positions if p.is_open]
        return events

    # ------------------------------------------------------------------ #
    # Entries
    # ------------------------------------------------------------------ #

    def _direction_for(self, alert: ScanAlert) -> Direction:
        # Reversal strategy: sell into call resistance, buy at put support.
        return Direction.SHORT if alert.signal is SignalType.CALL_OI else Direction.LONG

    def open_from_alerts(self, alerts: list[ScanAlert]) -> list[TradeEvent]:
        events: list[TradeEvent] = []
        held = {p.symbol for p in self.positions if p.is_open}

        for alert in alerts:
            if alert.symbol in held:
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
            )
            self.positions.append(position)
            held.add(alert.symbol)

            events.append(
                TradeEvent(
                    symbol=alert.symbol,
                    kind="entry",
                    detail=(
                        f"{direction.value} {lots} lot(s) x {alert.lot_size} @ "
                        f"₹{alert.ltp:,.2f} (RSI {alert.rsi:.1f}, strike ₹{alert.oi_strike:,.0f})"
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

    def summary(self, prices: dict[str, float]) -> str:
        open_pnl = self.unrealised(prices)
        total = self.realised_pnl + open_pnl
        equity = self.config.capital + total

        lines = [
            "Paper book",
            f"  Open positions   {len(self.positions)}",
            f"  Realised P&L     ₹{self.realised_pnl:,.0f} over {self.closed_count} closed trade(s)",
            f"  Unrealised P&L   ₹{open_pnl:,.0f}",
            f"  Equity           ₹{equity:,.0f} ({total / self.config.capital * 100:+.2f}%)",
            f"  Free capital     ₹{self.free_capital:,.0f}",
        ]
        return "\n".join(lines)
