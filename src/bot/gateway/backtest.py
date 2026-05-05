"""BacktestGateway: deterministic event-driven simulator matching the ABC.

Key model:
- Strategy sees bar close at time T. Any order placed during the T callback
  is filled at the OPEN of bar T+1 (with configured slippage). This avoids
  data-snooping and keeps backtest/paper semantics aligned.
- Fees applied per side as a fraction of notional.
- Fills are reported via the registered on_fill callback.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

import pandas as pd

from bot.core.clock import SimClock
from bot.core.enums import Interval, OrderSide, OrderStatus, OrderType
from bot.core.logging import get_logger
from bot.core.types import FeeSchedule, Fill, Order, OrderState, Position, SymbolMeta

from .base import ExchangeGateway

log = get_logger(__name__)


class BacktestGateway(ExchangeGateway):
    def __init__(
        self,
        bars: dict[str, pd.DataFrame],
        fee_per_side: float,
        slippage_bps: float,
        starting_cash: Decimal,
        quote_currency: str = "KRW",
    ) -> None:
        self._bars = bars
        self.fees = FeeSchedule(maker_bps=fee_per_side * 10_000, taker_bps=fee_per_side * 10_000)
        self._slippage = Decimal(str(slippage_bps / 10_000.0))
        self._cash: dict[str, Decimal] = {quote_currency: starting_cash}
        self._positions: dict[str, Position] = {}
        self._pending: list[tuple[str, Order]] = []  # filled at next bar open
        self._orders: dict[str, OrderState] = {}
        self._next_id = 0
        self._fill_cb: Callable[[Fill], None] | None = None
        # Build unified time index: union of all symbols' bar timestamps
        all_ts = sorted(set().union(*[set(df.index) for df in bars.values()]))
        self._timeline = all_ts
        self._clock = SimClock(all_ts[0] if all_ts else datetime.now(timezone.utc))
        self._quote = quote_currency

    def symbol_meta(self, symbol: str) -> SymbolMeta:
        # KRW spot defaults; production would fetch from exchange
        return SymbolMeta(
            symbol=symbol,
            base=symbol.split("-", 1)[1] if "-" in symbol else symbol,
            quote=self._quote,
            tick_size=Decimal("0.01"),
            lot_size=Decimal("0.00000001"),
            min_notional=Decimal("5000"),
        )

    def fetch_ohlcv(self, symbol, interval, since, until):
        df = self._bars[symbol]
        return df[(df.index >= since) & (df.index <= until)]

    def subscribe_bars(
        self,
        symbols: list[str],
        interval: Interval,
        callback: Callable[[str, pd.Series], None],
    ) -> None:
        for ts in self._timeline:
            self._clock.set(ts)
            # Step 1: settle any pending orders against THIS bar's open
            self._settle_pending(ts)
            # Step 2: deliver bar to strategy in symbol order
            for sym in symbols:
                df = self._bars.get(sym)
                if df is None or ts not in df.index:
                    continue
                callback(sym, df.loc[ts])
            # Step 3: mark trail stop high-watermarks for any open positions
            self._update_high_watermarks(ts)

    def _update_high_watermarks(self, ts) -> None:
        for sym, pos in self._positions.items():
            df = self._bars.get(sym)
            if df is None or ts not in df.index:
                continue
            high = Decimal(str(df.loc[ts, "high"]))
            if high > pos.high_watermark:
                pos.high_watermark = high

    def _settle_pending(self, ts) -> None:
        if not self._pending:
            return
        still: list[tuple[str, Order]] = []
        for oid, order in self._pending:
            df = self._bars.get(order.symbol)
            if df is None or ts not in df.index:
                still.append((oid, order))
                continue
            open_px = Decimal(str(df.loc[ts, "open"]))
            slip = self._slippage
            fill_px = open_px * (1 + slip) if order.side is OrderSide.BUY else open_px * (1 - slip)
            self._execute_fill(oid, order, fill_px, ts)
        self._pending = still

    def _execute_fill(self, oid: str, order: Order, price: Decimal, ts) -> None:
        notional = order.qty * price
        fee = notional * Decimal(str(self.fees.per_side(taker=True)))

        if order.side is OrderSide.BUY:
            cost = notional + fee
            if cost > self._cash.get(self._quote, Decimal(0)):
                self._orders[oid] = OrderState(oid, OrderStatus.REJECTED, Decimal(0), Decimal(0), order.qty)
                log.warning("backtest_reject_insufficient_cash", oid=oid, need=str(cost))
                return
            self._cash[self._quote] -= cost
            from bot.core.enums import Side
            self._positions[order.symbol] = Position(
                symbol=order.symbol,
                side=Side.LONG,
                qty=order.qty,
                entry_price=price,
                entry_ts=ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
                initial_stop=order.price or Decimal(0),  # filled in by allocator metadata
                trail_stop=order.price or Decimal(0),
                high_watermark=price,
            )
        else:  # SELL
            pos = self._positions.pop(order.symbol, None)
            if pos is None:
                self._orders[oid] = OrderState(oid, OrderStatus.REJECTED, Decimal(0), Decimal(0), order.qty)
                return
            proceeds = notional - fee
            self._cash[self._quote] = self._cash.get(self._quote, Decimal(0)) + proceeds

        self._orders[oid] = OrderState(oid, OrderStatus.FILLED, order.qty, price, Decimal(0))
        if self._fill_cb:
            self._fill_cb(Fill(oid, order.symbol, order.side, order.qty, price, fee, ts))

    def get_balances(self) -> dict[str, Decimal]:
        return dict(self._cash)

    def get_positions(self) -> list[Position]:
        return list(self._positions.values())

    def place_order(self, order: Order) -> str:
        if order.type is not OrderType.MARKET:
            raise NotImplementedError("BacktestGateway: only MARKET orders supported")
        self._next_id += 1
        oid = f"bt-{self._next_id}"
        self._orders[oid] = OrderState(oid, OrderStatus.PENDING, Decimal(0), Decimal(0), order.qty)
        self._pending.append((oid, order))
        return oid

    def cancel_order(self, order_id: str) -> bool:
        before = len(self._pending)
        self._pending = [(o, ord_) for (o, ord_) in self._pending if o != order_id]
        return len(self._pending) < before

    def get_order(self, order_id: str) -> OrderState:
        return self._orders[order_id]

    def now(self) -> datetime:
        return self._clock.now()
