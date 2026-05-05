"""Allocator: convert signals into orders, applying concentration / max_concurrent rules."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from bot.config.schema import RiskCfg
from bot.core.enums import OrderSide, OrderType
from bot.core.types import Order, Signal, SymbolMeta
from bot.risk.sizer import initial_stop, position_qty


@dataclass
class AllocatedOrder:
    order: Order
    initial_stop: Decimal


def allocate(
    signals: list[Signal],
    portfolio,
    risk: RiskCfg,
    metas: dict[str, SymbolMeta],
    equity: Decimal,
    can_enter: bool,
) -> list[AllocatedOrder]:
    """Convert prioritized signals into orders. Exits are unconditional;
    entries are gated by max_concurrent + can_enter + cash availability."""
    out: list[AllocatedOrder] = []
    held = set(portfolio.positions.keys())

    # 1) Exits first (always allowed)
    for sig in signals:
        if sig.enter:
            continue
        pos = portfolio.positions.get(sig.symbol)
        if pos is None:
            continue
        meta = metas.get(sig.symbol)
        if meta is None:
            continue
        out.append(AllocatedOrder(
            order=Order(
                symbol=sig.symbol,
                side=OrderSide.SELL,
                type=OrderType.MARKET,
                qty=pos.qty,
                quote_currency=meta.quote,
            ),
            initial_stop=Decimal(0),
        ))

    if not can_enter:
        return out

    # 2) Entries — sort by signal strength, then cap by concurrency budget
    entries = sorted([s for s in signals if s.enter and s.symbol not in held],
                     key=lambda s: s.strength, reverse=True)
    slots = max(0, risk.max_concurrent - len(held))
    for sig in entries[:slots]:
        meta = metas.get(sig.symbol)
        if meta is None:
            continue
        atr_v = Decimal(str(sig.meta.get("atr", 0)))
        entry_px = Decimal(str(sig.meta.get("entry_price", 0)))
        if entry_px <= 0:
            continue
        qty = position_qty(
            equity=equity,
            risk_per_trade=risk.risk_per_trade,
            entry_price=entry_px,
            atr_value=atr_v,
            atr_stop_mult=risk.atr_stop_mult,
            max_concentration=risk.max_concentration,
            min_notional=meta.min_notional,
            lot_size=meta.lot_size,
        )
        if qty <= 0:
            continue
        stop = initial_stop(entry_px, atr_v, risk.atr_stop_mult)
        out.append(AllocatedOrder(
            order=Order(
                symbol=sig.symbol,
                side=OrderSide.BUY,
                type=OrderType.MARKET,
                qty=qty,
                quote_currency=meta.quote,
            ),
            initial_stop=stop,
        ))
    return out
