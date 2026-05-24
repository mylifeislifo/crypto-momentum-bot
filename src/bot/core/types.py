from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional

from .enums import (
    Interval,
    NotifyEventType,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    SentimentLabel,
    Side,
)


@dataclass(frozen=True)
class OBLevel:
    price: Decimal
    qty: Decimal


@dataclass(frozen=True)
class OBSnapshot:
    ts: datetime
    bids: tuple[OBLevel, ...]   # sorted desc (best bid first)
    asks: tuple[OBLevel, ...]   # sorted asc (best ask first)
    imbalance_raw: float        # (bid_vol - ask_vol) / (bid_vol + ask_vol), pre-filter
    imbalance: float            # same, post spoof-filter
    mid_price: Decimal
    spread: Decimal


@dataclass(frozen=True)
class Trade:
    ts: datetime
    price: Decimal
    qty: Decimal
    is_buyer_maker: bool        # True = sell aggressor, False = buy aggressor


@dataclass(frozen=True)
class OIFunding:
    ts: datetime
    open_interest: Decimal      # in BTC (base asset)
    oi_delta_pct: float         # (curr - prev) / prev; None-safe via 0.0 on first tick
    funding_rate: float         # e.g. 0.0001 = 0.01%
    next_funding_ts: datetime


@dataclass(frozen=True)
class SentimentReading:
    ts: datetime
    fear_greed_index: int       # 0-100
    sentiment_label: SentimentLabel
    long_ratio: float           # e.g. 0.55 = 55% longs (from Coinglass)
    short_ratio: float


@dataclass(frozen=True)
class Bar:
    ts: datetime                # bar open time (UTC, floored to interval)
    interval: Interval
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal             # total traded qty
    buy_volume: Decimal         # aggressor buy qty
    sell_volume: Decimal        # aggressor sell qty
    cvd_delta: float            # buy_volume - sell_volume for this bar
    cvd_cumulative: float       # running CVD since bot start
    vwap: Decimal
    trade_count: int


@dataclass(frozen=True)
class Signal:
    ts: datetime
    side: Side
    entry_price_est: Decimal    # mid-price at signal time
    stop_price: Decimal         # initial SL price
    confidence: float           # 0.0-1.0 (fraction of gates passed with margin)
    # gate results
    macro_gate: bool            # sentiment + funding aligned
    micro_gate: bool            # OI delta + large OB fill
    cvd_gate: bool              # CVD trend confirmation
    # raw inputs for audit log
    fear_greed: int
    funding_rate: float
    oi_delta_pct: float
    imbalance: float
    cvd_delta_sum: float        # sum of last N bars CVD delta


@dataclass(frozen=True)
class Order:
    id: str
    client_order_id: str
    symbol: str
    side: OrderSide
    position_side: PositionSide
    order_type: OrderType
    qty: Decimal
    price: Optional[Decimal]
    stop_price: Optional[Decimal]
    status: OrderStatus
    ts: datetime
    reduce_only: bool = False


@dataclass(frozen=True)
class Fill:
    order_id: str
    symbol: str
    side: Side
    position_side: PositionSide
    qty: Decimal
    avg_price: Decimal
    commission: Decimal
    commission_asset: str
    ts: datetime
    is_entry: bool              # False = exit/SL hit


@dataclass
class Position:
    position_id: str            # UUID assigned by order_manager
    symbol: str
    side: Side
    position_side: PositionSide
    qty: Decimal
    entry_price: Decimal
    current_price: Decimal
    unrealized_pnl: Decimal
    leverage: int
    sl_order_id: str
    sl_price: Decimal
    opened_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class EquitySnapshot:
    ts: datetime
    balance: Decimal            # realized USDT balance
    unrealized_pnl: Decimal
    total_equity: Decimal
    daily_pnl_pct: float        # (total_equity - start_equity) / start_equity


@dataclass(frozen=True)
class CircuitBreakerState:
    triggered_at: datetime
    reset_at: datetime          # next 09:00 KST as UTC
    daily_pnl_pct: float
    message: str


@dataclass(frozen=True)
class TrailUpdate:
    position_id: str
    old_sl_order_id: str
    new_stop_price: Decimal
    old_stop_price: Decimal
    ts: datetime


@dataclass(frozen=True)
class NotifyEvent:
    event_type: NotifyEventType
    ts: datetime
    message: str
    data: Optional[dict] = None  # extra fields for structured logging
