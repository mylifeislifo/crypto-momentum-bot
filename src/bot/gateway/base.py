"""ExchangeGateway ABC. All modes (backtest/paper/live) implement this."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal
from typing import Callable, Optional

import pandas as pd

from bot.core.enums import Interval
from bot.core.types import FeeSchedule, Fill, Order, OrderState, Position, SymbolMeta


class ExchangeGateway(ABC):
    """Single interface that strategy/risk/portfolio code talks to.

    Backtest, paper, and live implementations differ only in how data is fed
    and how orders are routed. The strategy/portfolio layer is mode-agnostic.
    """

    fees: FeeSchedule

    @abstractmethod
    def symbol_meta(self, symbol: str) -> SymbolMeta: ...

    # --- data
    @abstractmethod
    def fetch_ohlcv(
        self, symbol: str, interval: Interval, since: datetime, until: datetime
    ) -> pd.DataFrame: ...

    @abstractmethod
    def subscribe_bars(
        self,
        symbols: list[str],
        interval: Interval,
        callback: Callable[[str, pd.Series], None],
    ) -> None:
        """Drive the strategy. In backtest = iterate historical bars."""

    # --- account
    @abstractmethod
    def get_balances(self) -> dict[str, Decimal]: ...

    @abstractmethod
    def get_positions(self) -> list[Position]: ...

    # --- orders
    @abstractmethod
    def place_order(self, order: Order) -> str: ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool: ...

    @abstractmethod
    def get_order(self, order_id: str) -> OrderState: ...

    # --- time
    @abstractmethod
    def now(self) -> datetime: ...

    # --- optional (Phase 4 derivatives)
    def set_leverage(self, symbol: str, leverage: float) -> None:
        raise NotImplementedError("Spot exchange does not support leverage")

    def set_margin_mode(self, symbol: str, mode: str) -> None:
        raise NotImplementedError("Spot exchange does not support margin mode")

    # --- callbacks for execution layer
    def on_fill(self, callback: Callable[[Fill], None]) -> None:
        self._fill_cb: Optional[Callable[[Fill], None]] = callback
