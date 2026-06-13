"""Abstract gateway interface for Binance Futures."""

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Optional

from ..core.enums import MarginType, OrderSide, OrderType, PositionSide
from ..core.types import Fill, Order, Position


class FuturesGateway(ABC):

    async def connect(self) -> None:
        """Open any network session. No-op for gateways that don't need one (paper)."""
        return None

    async def disconnect(self) -> None:
        """Close any network session. No-op by default."""
        return None

    @abstractmethod
    async def set_leverage(self, symbol: str, leverage: int) -> None: ...

    @abstractmethod
    async def set_margin_mode(self, symbol: str, margin_type: MarginType) -> None: ...

    @abstractmethod
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
    ) -> Order: ...

    @abstractmethod
    async def cancel_order(self, symbol: str, order_id: str) -> None: ...

    @abstractmethod
    async def get_position(self, symbol: str) -> Optional[Position]: ...

    @abstractmethod
    async def get_balance(self) -> Decimal:
        """Return available USDT balance."""
        ...

    @abstractmethod
    async def close_all_positions(self, symbol: str) -> list[Fill]:
        """Market-close every open position for symbol."""
        ...

    @property
    def is_paper(self) -> bool:
        return False
