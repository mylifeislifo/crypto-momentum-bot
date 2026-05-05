"""Core dataclasses shared across modes (backtest/paper/live)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional

from .enums import OrderSide, OrderStatus, OrderType, Regime, Side, TimeInForce


@dataclass(frozen=True)
class Bar:
    symbol: str
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class FeeSchedule:
    maker_bps: float
    taker_bps: float

    def per_side(self, taker: bool = True) -> float:
        return (self.taker_bps if taker else self.maker_bps) / 10_000.0


@dataclass(frozen=True)
class SymbolMeta:
    symbol: str
    base: str
    quote: str
    tick_size: Decimal
    lot_size: Decimal
    min_notional: Decimal
    is_derivative: bool = False


@dataclass(frozen=True)
class Signal:
    symbol: str
    ts: datetime
    side: Side
    strength: float  # ranking key (e.g. ADX), used to break ties
    reason: str
    enter: bool  # True=open, False=close
    meta: dict = field(default_factory=dict)


@dataclass
class Order:
    symbol: str
    side: OrderSide
    type: OrderType
    qty: Decimal
    price: Optional[Decimal] = None  # None for market
    tif: TimeInForce = TimeInForce.GTC
    client_id: Optional[str] = None
    quote_currency: str = "KRW"
    is_derivative: bool = False
    leverage: float = 1.0
    reduce_only: bool = False


@dataclass
class Fill:
    order_id: str
    symbol: str
    side: OrderSide
    qty: Decimal
    price: Decimal
    fee: Decimal
    ts: datetime


@dataclass
class OrderState:
    order_id: str
    status: OrderStatus
    filled_qty: Decimal
    avg_price: Decimal
    remaining_qty: Decimal


@dataclass
class Position:
    symbol: str
    side: Side
    qty: Decimal
    entry_price: Decimal
    entry_ts: datetime
    initial_stop: Decimal
    trail_stop: Decimal
    high_watermark: Decimal  # max high since entry (for chandelier trail)
    is_derivative: bool = False
    leverage: float = 1.0

    def notional(self, mark: Decimal) -> Decimal:
        return self.qty * mark

    def unrealized_pnl(self, mark: Decimal) -> Decimal:
        sign = Decimal(1) if self.side is Side.LONG else Decimal(-1)
        return (mark - self.entry_price) * self.qty * sign


@dataclass
class EquitySnapshot:
    ts: datetime
    cash: Decimal
    positions_value: Decimal
    equity: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal


@dataclass
class RegimeState:
    regime: Regime
    ts: datetime
    meta: dict = field(default_factory=dict)
