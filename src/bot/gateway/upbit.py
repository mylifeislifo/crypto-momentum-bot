"""Live Upbit gateway via pyupbit. Spot KRW market only.

Safety:
  - dry_run=True intercepts place_order and logs without sending.
  - Always reconciles balance just before submitting BUY orders.
  - Market BUY uses KRW notional; Market SELL uses base quantity.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

import pandas as pd

from bot.core.clock import SystemClock
from bot.core.enums import Interval, OrderSide, OrderStatus, OrderType, Side
from bot.core.logging import get_logger
from bot.core.types import FeeSchedule, Fill, Order, OrderState, Position, SymbolMeta

from .base import ExchangeGateway

log = get_logger(__name__)


class UpbitGateway(ExchangeGateway):
    def __init__(
        self,
        access_key: str | None,
        secret_key: str | None,
        fee_per_side: float,
        slippage_bps: float,
        dry_run: bool = True,
    ) -> None:
        self.fees = FeeSchedule(maker_bps=fee_per_side * 10_000, taker_bps=fee_per_side * 10_000)
        self._slippage_bps = slippage_bps
        self._dry_run = dry_run
        self._clock = SystemClock()
        self._fill_cb: Callable[[Fill], None] | None = None
        self._orders: dict[str, OrderState] = {}
        self._upbit = None
        if not dry_run:
            if not access_key or not secret_key:
                raise RuntimeError("UpbitGateway: API keys required for non-dry-run mode")
            import pyupbit
            self._upbit = pyupbit.Upbit(access_key, secret_key)

    def symbol_meta(self, symbol: str) -> SymbolMeta:
        return SymbolMeta(
            symbol=symbol, base=symbol.split("-", 1)[1], quote="KRW",
            tick_size=Decimal("0.01"),
            lot_size=Decimal("0.00000001"),
            min_notional=Decimal("5000"),
        )

    def fetch_ohlcv(self, symbol, interval, since, until):
        import pyupbit
        df = pyupbit.get_ohlcv(symbol, interval=interval.value, count=200)
        if df is None or df.empty:
            return pd.DataFrame()
        df = df[["open", "high", "low", "close", "volume"]].copy()
        df.index = pd.to_datetime(df.index).tz_localize("Asia/Seoul").tz_convert("UTC")
        return df

    def subscribe_bars(self, symbols, interval, callback):
        raise NotImplementedError("Use bot.live.scheduler with UpbitGateway")

    def get_balances(self) -> dict[str, Decimal]:
        if self._dry_run or self._upbit is None:
            return {"KRW": Decimal(0)}
        out: dict[str, Decimal] = {}
        for b in self._upbit.get_balances() or []:
            currency = b["currency"]
            out[currency] = Decimal(str(b.get("balance", 0)))
        return out

    def get_positions(self) -> list[Position]:
        positions: list[Position] = []
        if self._dry_run or self._upbit is None:
            return positions
        ts = self._clock.now()
        for b in self._upbit.get_balances() or []:
            currency = b["currency"]
            if currency == "KRW":
                continue
            qty = Decimal(str(b.get("balance", 0)))
            avg = Decimal(str(b.get("avg_buy_price", 0)))
            if qty <= 0:
                continue
            symbol = f"KRW-{currency}"
            positions.append(Position(
                symbol=symbol, side=Side.LONG, qty=qty,
                entry_price=avg, entry_ts=ts,
                initial_stop=Decimal(0), trail_stop=Decimal(0), high_watermark=avg,
            ))
        return positions

    def place_order(self, order: Order) -> str:
        if order.type is not OrderType.MARKET:
            raise NotImplementedError("UpbitGateway: only MARKET orders supported")
        oid = f"upbit-{uuid.uuid4().hex[:12]}"
        if self._dry_run or self._upbit is None:
            log.info("dry_run_order", oid=oid, symbol=order.symbol, side=order.side.value,
                     qty=str(order.qty))
            self._orders[oid] = OrderState(oid, OrderStatus.FILLED, order.qty, Decimal(0), Decimal(0))
            return oid

        if order.side is OrderSide.BUY:
            # Upbit market BUY = krw notional. Reconcile balances first.
            balances = self.get_balances()
            cash = balances.get("KRW", Decimal(0))
            # Pull current ask to estimate notional; cap at available cash
            import pyupbit
            ticker = pyupbit.get_current_price(order.symbol)
            if ticker is None:
                raise RuntimeError(f"No price for {order.symbol}")
            estimated = order.qty * Decimal(str(ticker)) * Decimal("1.001")  # small headroom
            notional = min(cash, estimated)
            if notional < Decimal("5000"):
                self._orders[oid] = OrderState(oid, OrderStatus.REJECTED, Decimal(0), Decimal(0), order.qty)
                return oid
            resp = self._upbit.buy_market_order(order.symbol, float(notional))
            log.info("upbit_buy_submitted", resp=resp, oid=oid)
        else:  # SELL
            resp = self._upbit.sell_market_order(order.symbol, float(order.qty))
            log.info("upbit_sell_submitted", resp=resp, oid=oid)

        self._orders[oid] = OrderState(oid, OrderStatus.OPEN, Decimal(0), Decimal(0), order.qty)
        time.sleep(0.5)  # small wait then poll once for fill
        self._poll_fill(oid, order)
        return oid

    def _poll_fill(self, oid: str, order: Order) -> None:
        # Best-effort: rely on balance changes; production should use exchange UUIDs
        balances = self.get_balances()
        if order.side is OrderSide.BUY:
            currency = order.symbol.split("-", 1)[1]
            qty = balances.get(currency, Decimal(0))
            if qty > 0:
                import pyupbit
                price = Decimal(str(pyupbit.get_current_price(order.symbol)))
                fill = Fill(oid, order.symbol, order.side, qty, price,
                            qty * price * Decimal(str(self.fees.per_side(taker=True))),
                            self._clock.now())
                self._orders[oid] = OrderState(oid, OrderStatus.FILLED, qty, price, Decimal(0))
                if self._fill_cb:
                    self._fill_cb(fill)
        else:
            import pyupbit
            price = Decimal(str(pyupbit.get_current_price(order.symbol)))
            fill = Fill(oid, order.symbol, order.side, order.qty, price,
                        order.qty * price * Decimal(str(self.fees.per_side(taker=True))),
                        self._clock.now())
            self._orders[oid] = OrderState(oid, OrderStatus.FILLED, order.qty, price, Decimal(0))
            if self._fill_cb:
                self._fill_cb(fill)

    def cancel_order(self, order_id: str) -> bool:
        if self._dry_run or self._upbit is None:
            return True
        # Production: keep exchange UUID mapping; placeholder here
        return False

    def get_order(self, order_id: str) -> OrderState:
        return self._orders[order_id]

    def now(self) -> datetime:
        return self._clock.now()
