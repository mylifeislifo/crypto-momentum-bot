"""Portfolio: source of truth for cash, positions, equity. Updated by gateway fills."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from bot.core.enums import OrderSide, Side
from bot.core.types import EquitySnapshot, Fill, Position


@dataclass
class Portfolio:
    quote: str = "KRW"
    cash: Decimal = Decimal(0)
    positions: dict[str, Position] = field(default_factory=dict)
    realized_pnl: Decimal = Decimal(0)
    last_exit_ts: dict[str, datetime] = field(default_factory=dict)
    equity_curve: list[EquitySnapshot] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)

    def apply_fill(self, fill: Fill, initial_stop: Decimal | None = None) -> None:
        if fill.side is OrderSide.BUY:
            cost = fill.qty * fill.price + fill.fee
            self.cash -= cost
            self.positions[fill.symbol] = Position(
                symbol=fill.symbol,
                side=Side.LONG,
                qty=fill.qty,
                entry_price=fill.price,
                entry_ts=fill.ts,
                initial_stop=initial_stop or Decimal(0),
                trail_stop=initial_stop or Decimal(0),
                high_watermark=fill.price,
            )
            self.trades.append({
                "ts": fill.ts, "symbol": fill.symbol, "side": "buy",
                "qty": float(fill.qty), "price": float(fill.price), "fee": float(fill.fee),
            })
        else:  # SELL
            pos = self.positions.pop(fill.symbol, None)
            proceeds = fill.qty * fill.price - fill.fee
            self.cash += proceeds
            if pos is not None:
                pnl = (fill.price - pos.entry_price) * fill.qty - fill.fee
                self.realized_pnl += pnl
                self.last_exit_ts[fill.symbol] = fill.ts
                self.trades.append({
                    "ts": fill.ts, "symbol": fill.symbol, "side": "sell",
                    "qty": float(fill.qty), "price": float(fill.price), "fee": float(fill.fee),
                    "pnl": float(pnl), "entry_price": float(pos.entry_price),
                    "entry_ts": pos.entry_ts,
                })

    def equity(self, marks: dict[str, Decimal]) -> Decimal:
        positions_value = sum(
            (pos.qty * marks.get(sym, pos.entry_price) for sym, pos in self.positions.items()),
            start=Decimal(0),
        )
        return self.cash + positions_value

    def snapshot(self, ts: datetime, marks: dict[str, Decimal]) -> EquitySnapshot:
        positions_value = sum(
            (pos.qty * marks.get(sym, pos.entry_price) for sym, pos in self.positions.items()),
            start=Decimal(0),
        )
        unrealized = sum(
            (pos.unrealized_pnl(marks.get(sym, pos.entry_price)) for sym, pos in self.positions.items()),
            start=Decimal(0),
        )
        snap = EquitySnapshot(
            ts=ts,
            cash=self.cash,
            positions_value=positions_value,
            equity=self.cash + positions_value,
            realized_pnl=self.realized_pnl,
            unrealized_pnl=unrealized,
        )
        self.equity_curve.append(snap)
        return snap
