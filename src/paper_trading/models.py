from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class ExitReason(str, Enum):
    FIRST_TARGET = "first_target"
    SECOND_TARGET = "second_target"
    THIRD_TARGET = "third_target"
    RSI_TARGET = "rsi_target"
    STOP_LOSS = "stop_loss"
    EXPIRY = "expiry"
    WALL_BROKEN = "wall_broken"
    STRIKE_THROUGH = "strike_through"
    WRITING_GONE = "writing_gone"


@dataclass
class ClosedLeg:
    lots: int
    exit_price: float
    exit_time: str
    reason: str
    pnl: float


@dataclass
class Position:
    symbol: str
    direction: str
    entry_price: float
    entry_time: str
    lot_size: int
    lots_open: int
    lots_total: int
    expiry: str
    rsi_at_entry: float
    strike: float
    margin_blocked: float
    closed_legs: list[ClosedLeg] = field(default_factory=list)
    # "equity" until the first 3rd-month fut quote; then "futures".
    priced_on: str = "equity"
    # Last scan's OI invalidation reason (S2 strike/writing; S1 OI-flow).
    # Empty = wall still valid. Exit only after a second consecutive invalid scan.
    s2_invalid_pending: str = ""
    # RSI_CandlePattern: bar high (short) or low (long). None = use percent stop.
    stop_price: float | None = None

    def move_pct(self, price: float) -> float:
        """Percent moved in the trade's favour; negative means against it."""
        if self.entry_price <= 0:
            return 0.0
        raw = (price - self.entry_price) / self.entry_price * 100
        return raw if self.direction == Direction.LONG else -raw

    def price_at_move(self, move_pct: float) -> float:
        """The price at which the trade shows the given favourable move."""
        sign = 1 if self.direction == Direction.LONG else -1
        return self.entry_price * (1 + sign * move_pct / 100)

    def pnl_for(self, lots: int, exit_price: float) -> float:
        difference = exit_price - self.entry_price
        if self.direction == Direction.SHORT:
            difference = -difference
        return difference * self.lot_size * lots

    def unrealised(self, price: float) -> float:
        return self.pnl_for(self.lots_open, price)

    @property
    def realised(self) -> float:
        return sum(leg.pnl for leg in self.closed_legs)

    @property
    def is_open(self) -> bool:
        return self.lots_open > 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> Position:
        legs = [ClosedLeg(**leg) for leg in raw.pop("closed_legs", [])]
        return cls(**raw, closed_legs=legs)


@dataclass
class TradeEvent:
    """Something that happened this scan and is worth reporting."""

    symbol: str
    kind: str
    detail: str
    pnl: float = 0.0


def now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
