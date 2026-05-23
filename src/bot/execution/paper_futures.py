"""Paper trading gateway — in-memory fills, JSON state persistence.

Fills MARKET orders immediately at latest_price ± slippage_bps.
Records STOP_MARKET orders; order_manager checks crossings on each OB tick.
State persists to paper_state.json so a restart recovers open positions.
"""

import json
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

import structlog

from ..core.clock import utc_now
from ..core.enums import MarginType, OrderSide, OrderStatus, OrderType, PositionSide, Side
from ..core.types import Fill, Order, Position
from .gateway_base import FuturesGateway

logger = structlog.get_logger(__name__)

_STATE_FILE = Path("paper_state.json")
_DEFAULT_SLIPPAGE_BPS = Decimal("5")   # 0.05% per side


@dataclass
class _PaperPosition:
    position_id: str
    symbol: str
    side: str           # Side enum value
    position_side: str  # PositionSide enum value
    qty: str            # Decimal as str
    entry_price: str
    sl_price: str
    sl_order_id: str
    opened_at: str


class PaperFuturesGateway(FuturesGateway):
    def __init__(
        self,
        initial_balance: Decimal = Decimal("10000"),
        slippage_bps: Decimal = _DEFAULT_SLIPPAGE_BPS,
        state_file: Path = _STATE_FILE,
    ) -> None:
        self._balance = initial_balance
        self._slippage = slippage_bps / Decimal("10000")
        self._state_file = state_file
        self._positions: dict[str, _PaperPosition] = {}     # position_id → state
        self._stop_orders: dict[str, dict] = {}             # sl_order_id → details
        self._order_counter = 0
        self._latest_price: Decimal = Decimal("0")
        self._load_state()

    # ------------------------------------------------------------------
    # FuturesGateway interface
    # ------------------------------------------------------------------

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        logger.info("paper.set_leverage", symbol=symbol, leverage=leverage)

    async def set_margin_mode(self, symbol: str, margin_type: MarginType) -> None:
        logger.info("paper.set_margin_mode", symbol=symbol, margin_type=margin_type.value)

    async def place_order(
        self,
        symbol: str,
        side: OrderSide,
        position_side: PositionSide,
        order_type: OrderType,
        qty: Decimal,
        price: Optional[Decimal] = None,
        stop_price: Optional[Decimal] = None,
        reduce_only: bool = False,
        client_order_id: Optional[str] = None,
    ) -> Order:
        self._order_counter += 1
        order_id = f"paper_{self._order_counter:06d}"
        coid = client_order_id or f"C{order_id}"
        now = utc_now()

        if order_type == OrderType.MARKET:
            fill_price = self._fill_price(side)
            notional = fill_price * qty

            if side == OrderSide.BUY and not reduce_only:
                self._balance -= notional
            elif side == OrderSide.SELL and not reduce_only:
                self._balance -= notional  # margin reserved
            elif reduce_only:
                self._close_position_by_side(position_side, qty, fill_price)

            logger.info(
                "paper.market_fill",
                order_id=order_id,
                side=side.value,
                qty=str(qty),
                price=str(fill_price),
            )

        elif order_type == OrderType.STOP_MARKET and stop_price is not None:
            self._stop_orders[order_id] = {
                "order_id": order_id,
                "symbol": symbol,
                "side": side.value,
                "position_side": position_side.value,
                "qty": str(qty),
                "stop_price": str(stop_price),
                "reduce_only": reduce_only,
            }
            logger.info(
                "paper.stop_registered",
                order_id=order_id,
                stop_price=str(stop_price),
                side=side.value,
            )

        return Order(
            id=order_id,
            client_order_id=coid,
            symbol=symbol,
            side=side,
            position_side=position_side,
            order_type=order_type,
            qty=qty,
            price=price,
            stop_price=stop_price,
            status=OrderStatus.FILLED if order_type == OrderType.MARKET else OrderStatus.NEW,
            ts=now,
            reduce_only=reduce_only,
        )

    async def cancel_order(self, symbol: str, order_id: str) -> None:
        self._stop_orders.pop(order_id, None)
        logger.debug("paper.order_cancelled", order_id=order_id)

    async def get_position(self, symbol: str) -> Optional[Position]:
        for pos in self._positions.values():
            if pos.symbol == symbol:
                qty = Decimal(pos.qty)
                entry = Decimal(pos.entry_price)
                pnl = (self._latest_price - entry) * qty if pos.side == Side.LONG.value \
                    else (entry - self._latest_price) * qty
                return Position(
                    position_id=pos.position_id,
                    symbol=pos.symbol,
                    side=Side(pos.side),
                    position_side=PositionSide(pos.position_side),
                    qty=qty,
                    entry_price=entry,
                    current_price=self._latest_price,
                    unrealized_pnl=pnl,
                    leverage=2,
                    sl_order_id=pos.sl_order_id,
                    sl_price=Decimal(pos.sl_price),
                    opened_at=datetime.fromisoformat(pos.opened_at),
                    updated_at=utc_now(),
                )
        return None

    async def get_balance(self) -> Decimal:
        return self._balance

    async def close_all_positions(self, symbol: str) -> list[Fill]:
        fills = []
        for pos_id, pos in list(self._positions.items()):
            fill_price = self._fill_price(
                OrderSide.SELL if pos.side == Side.LONG.value else OrderSide.BUY
            )
            qty = Decimal(pos.qty)
            pnl = (fill_price - Decimal(pos.entry_price)) * qty if pos.side == Side.LONG.value \
                else (Decimal(pos.entry_price) - fill_price) * qty
            self._balance += pnl
            self._positions.pop(pos_id)
            fills.append(Fill(
                order_id=f"close_{pos_id}",
                symbol=symbol,
                side=Side(pos.side),
                position_side=PositionSide(pos.position_side),
                qty=qty,
                avg_price=fill_price,
                commission=qty * fill_price * Decimal("0.0004"),
                commission_asset="USDT",
                ts=utc_now(),
                is_entry=False,
            ))
            logger.info("paper.position_closed", position_id=pos_id, pnl=str(pnl))
        self._save_state()
        return fills

    @property
    def is_paper(self) -> bool:
        return True

    # ------------------------------------------------------------------
    # Paper-specific helpers
    # ------------------------------------------------------------------

    def update_price(self, mid_price: Decimal) -> None:
        """Called by order_manager on each OB snapshot."""
        self._latest_price = mid_price

    def register_position(
        self,
        position_id: str,
        symbol: str,
        side: Side,
        position_side: PositionSide,
        qty: Decimal,
        entry_price: Decimal,
        sl_price: Decimal,
        sl_order_id: str,
    ) -> None:
        self._positions[position_id] = _PaperPosition(
            position_id=position_id,
            symbol=symbol,
            side=side.value,
            position_side=position_side.value,
            qty=str(qty),
            entry_price=str(entry_price),
            sl_price=str(sl_price),
            sl_order_id=sl_order_id,
            opened_at=utc_now().isoformat(),
        )
        self._save_state()

    def close_position(self, position_id: str) -> Optional[Fill]:
        pos = self._positions.pop(position_id, None)
        if not pos:
            return None
        fill_price = self._fill_price(
            OrderSide.SELL if pos.side == Side.LONG.value else OrderSide.BUY
        )
        qty = Decimal(pos.qty)
        pnl = (fill_price - Decimal(pos.entry_price)) * qty if pos.side == Side.LONG.value \
            else (Decimal(pos.entry_price) - fill_price) * qty
        self._balance += pnl
        self._stop_orders.pop(pos.sl_order_id, None)
        self._save_state()
        logger.info("paper.position_closed", position_id=position_id, fill=str(fill_price), pnl=str(pnl))
        return Fill(
            order_id=f"sl_{position_id}",
            symbol=pos.symbol,
            side=Side(pos.side),
            position_side=PositionSide(pos.position_side),
            qty=qty,
            avg_price=fill_price,
            commission=qty * fill_price * Decimal("0.0004"),
            commission_asset="USDT",
            ts=utc_now(),
            is_entry=False,
        )

    def get_triggered_stops(self, mid_price: Decimal) -> list[str]:
        """Return position_ids whose SL stop has been crossed."""
        triggered = []
        for pos_id, pos in self._positions.items():
            sl = Decimal(pos.sl_price)
            if pos.side == Side.LONG.value and mid_price <= sl:
                triggered.append(pos_id)
            elif pos.side == Side.SHORT.value and mid_price >= sl:
                triggered.append(pos_id)
        return triggered

    def update_sl_price(self, position_id: str, new_sl_price: Decimal, new_sl_order_id: str) -> None:
        if pos := self._positions.get(position_id):
            self._stop_orders.pop(pos.sl_order_id, None)
            pos.sl_price = str(new_sl_price)
            pos.sl_order_id = new_sl_order_id
            self._save_state()

    @property
    def active_position_ids(self) -> list[str]:
        return list(self._positions.keys())

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _fill_price(self, side: OrderSide) -> Decimal:
        if self._latest_price == 0:
            return Decimal("50000")
        slippage_mult = (Decimal("1") + self._slippage) if side == OrderSide.BUY \
            else (Decimal("1") - self._slippage)
        return (self._latest_price * slippage_mult).quantize(Decimal("0.01"))

    def _close_position_by_side(self, position_side: PositionSide, qty: Decimal, fill_price: Decimal) -> None:
        for pos_id, pos in list(self._positions.items()):
            if PositionSide(pos.position_side) == position_side:
                pnl = (fill_price - Decimal(pos.entry_price)) * qty if pos.side == Side.LONG.value \
                    else (Decimal(pos.entry_price) - fill_price) * qty
                self._balance += pnl
                self._positions.pop(pos_id)
                break

    def _save_state(self) -> None:
        try:
            data = {
                "balance": str(self._balance),
                "positions": {pid: asdict(p) for pid, p in self._positions.items()},
            }
            self._state_file.write_text(json.dumps(data, indent=2))
        except Exception as exc:
            logger.warning("paper.state_save_failed", error=str(exc))

    def _load_state(self) -> None:
        if not self._state_file.exists():
            return
        try:
            data = json.loads(self._state_file.read_text())
            self._balance = Decimal(data.get("balance", str(self._balance)))
            for pid, p in data.get("positions", {}).items():
                self._positions[pid] = _PaperPosition(**p)
            logger.info("paper.state_loaded", balance=str(self._balance), positions=len(self._positions))
        except Exception as exc:
            logger.warning("paper.state_load_failed", error=str(exc))
