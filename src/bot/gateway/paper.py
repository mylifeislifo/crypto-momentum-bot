"""PaperGateway: live Upbit market data, simulated order matching.

Order flow:
  - place_order is queued. It's filled at the moment the next bar closes
    (subscribe_bars callback), against the latest orderbook snapshot.
  - Buy fills walk asks; sell fills walk bids. Extra slippage is applied.
  - Cash and positions tracked in memory; snapshots persisted as JSON for
    crash recovery.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable

import pandas as pd

from bot.core.clock import SystemClock
from bot.core.enums import Interval, OrderSide, OrderStatus, OrderType, Side
from bot.core.logging import get_logger
from bot.core.types import FeeSchedule, Fill, Order, OrderState, Position, SymbolMeta
from bot.execution.slippage import orderbook_fill_price

from .base import ExchangeGateway

log = get_logger(__name__)


class PaperGateway(ExchangeGateway):
    def __init__(
        self,
        starting_cash_krw: Decimal,
        fee_per_side: float,
        slippage_bps: float,
        state_path: Path | None = None,
        quote: str = "KRW",
    ) -> None:
        self.fees = FeeSchedule(maker_bps=fee_per_side * 10_000, taker_bps=fee_per_side * 10_000)
        self._slippage_bps = slippage_bps
        self._cash: dict[str, Decimal] = {quote: starting_cash_krw}
        self._positions: dict[str, Position] = {}
        self._pending: list[tuple[str, Order]] = []
        self._orders: dict[str, OrderState] = {}
        self._next_id = 0
        self._fill_cb: Callable[[Fill], None] | None = None
        self._clock = SystemClock()
        self._state_path = state_path
        self._quote = quote
        self._restore()

    # --- persistence (simple snapshot) ---
    def _restore(self) -> None:
        if self._state_path is None or not self._state_path.exists():
            return
        try:
            with self._state_path.open() as f:
                state = json.load(f)
            self._cash = {k: Decimal(v) for k, v in state.get("cash", {}).items()}
            for sym, p in state.get("positions", {}).items():
                self._positions[sym] = Position(
                    symbol=sym, side=Side.LONG,
                    qty=Decimal(p["qty"]),
                    entry_price=Decimal(p["entry_price"]),
                    entry_ts=datetime.fromisoformat(p["entry_ts"]),
                    initial_stop=Decimal(p["initial_stop"]),
                    trail_stop=Decimal(p["trail_stop"]),
                    high_watermark=Decimal(p["high_watermark"]),
                )
            log.info("paper_state_restored", positions=len(self._positions))
        except Exception as e:
            log.warning("paper_state_restore_failed", err=str(e))

    def _persist(self) -> None:
        if self._state_path is None:
            return
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "cash": {k: str(v) for k, v in self._cash.items()},
            "positions": {
                sym: {
                    "qty": str(p.qty),
                    "entry_price": str(p.entry_price),
                    "entry_ts": p.entry_ts.isoformat(),
                    "initial_stop": str(p.initial_stop),
                    "trail_stop": str(p.trail_stop),
                    "high_watermark": str(p.high_watermark),
                }
                for sym, p in self._positions.items()
            },
        }
        with self._state_path.open("w") as f:
            json.dump(state, f, indent=2)

    # --- ABC ---
    def symbol_meta(self, symbol: str) -> SymbolMeta:
        # Upbit KRW spot defaults; production should fetch market info
        return SymbolMeta(
            symbol=symbol, base=symbol.split("-", 1)[1], quote=self._quote,
            tick_size=Decimal("0.01"),
            lot_size=Decimal("0.00000001"),
            min_notional=Decimal("5000"),
        )

    def fetch_ohlcv(self, symbol, interval, since, until):
        import pyupbit
        # For paper we typically rely on subscribe_bars, but allow ad-hoc fetch
        df = pyupbit.get_ohlcv(symbol, interval=interval.value, count=200)
        if df is None or df.empty:
            return pd.DataFrame()
        df = df[["open", "high", "low", "close", "volume"]].copy()
        df.index = pd.to_datetime(df.index).tz_localize("Asia/Seoul").tz_convert("UTC")
        return df

    def subscribe_bars(self, symbols, interval, callback):
        # Implemented by live/scheduler.py calling _on_bar; this stub is unused
        raise NotImplementedError("Use bot.live.scheduler with PaperGateway")

    def get_balances(self) -> dict[str, Decimal]:
        return dict(self._cash)

    def get_positions(self) -> list[Position]:
        return list(self._positions.values())

    def place_order(self, order: Order) -> str:
        if order.type is not OrderType.MARKET:
            raise NotImplementedError("PaperGateway: only MARKET orders supported")
        self._next_id += 1
        oid = f"paper-{self._next_id}"
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

    # --- driven by scheduler ---
    def settle_pending(self) -> list[Fill]:
        """Called by scheduler after a bar closes. Pulls live orderbook for each
        pending order, computes a VWAP-walked fill, updates state, returns Fills."""
        if not self._pending:
            return []
        import pyupbit
        fills: list[Fill] = []
        still: list[tuple[str, Order]] = []
        for oid, order in self._pending:
            book = pyupbit.get_orderbook(order.symbol)
            if not book:
                still.append((oid, order))
                continue
            units = book.get("orderbook_units") or book[0].get("orderbook_units")
            asks = [(Decimal(str(u["ask_price"])), Decimal(str(u["ask_size"]))) for u in units]
            bids = [(Decimal(str(u["bid_price"])), Decimal(str(u["bid_size"]))) for u in units]
            try:
                px = orderbook_fill_price(
                    side_buy=order.side is OrderSide.BUY,
                    qty=order.qty,
                    levels=asks if order.side is OrderSide.BUY else bids,
                    extra_slippage_bps=self._slippage_bps,
                )
            except Exception as e:
                log.warning("paper_fill_failed", err=str(e), oid=oid)
                still.append((oid, order))
                continue
            fill = self._book_fill(oid, order, px)
            if fill is not None:
                fills.append(fill)
        self._pending = still
        self._persist()
        return fills

    def _book_fill(self, oid: str, order: Order, price: Decimal) -> Fill | None:
        notional = order.qty * price
        fee = notional * Decimal(str(self.fees.per_side(taker=True)))
        ts = self._clock.now()
        if order.side is OrderSide.BUY:
            cost = notional + fee
            if cost > self._cash.get(self._quote, Decimal(0)):
                self._orders[oid] = OrderState(oid, OrderStatus.REJECTED, Decimal(0), Decimal(0), order.qty)
                log.warning("paper_reject_insufficient_cash", oid=oid)
                return None
            self._cash[self._quote] -= cost
            self._positions[order.symbol] = Position(
                symbol=order.symbol, side=Side.LONG,
                qty=order.qty, entry_price=price, entry_ts=ts,
                initial_stop=Decimal(0), trail_stop=Decimal(0), high_watermark=price,
            )
        else:
            pos = self._positions.pop(order.symbol, None)
            if pos is None:
                self._orders[oid] = OrderState(oid, OrderStatus.REJECTED, Decimal(0), Decimal(0), order.qty)
                return None
            self._cash[self._quote] = self._cash.get(self._quote, Decimal(0)) + notional - fee

        self._orders[oid] = OrderState(oid, OrderStatus.FILLED, order.qty, price, Decimal(0))
        fill = Fill(oid, order.symbol, order.side, order.qty, price, fee, ts)
        if self._fill_cb:
            self._fill_cb(fill)
        return fill
