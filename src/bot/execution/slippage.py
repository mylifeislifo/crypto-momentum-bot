"""Slippage models for paper / backtest fills."""
from __future__ import annotations

from decimal import Decimal


def market_fill_price(side_buy: bool, mid_or_open: Decimal, slippage_bps: float) -> Decimal:
    slip = Decimal(str(slippage_bps / 10_000.0))
    return mid_or_open * (1 + slip) if side_buy else mid_or_open * (1 - slip)


def orderbook_fill_price(
    side_buy: bool,
    qty: Decimal,
    levels: list[tuple[Decimal, Decimal]],  # [(price, size), ...] best first
    extra_slippage_bps: float = 0.0,
) -> Decimal:
    """VWAP-walk through the levels until qty is filled. Adds extra slippage on top."""
    if not levels:
        raise ValueError("Empty orderbook side")
    remaining = qty
    cost = Decimal(0)
    for px, sz in levels:
        take = min(remaining, sz)
        cost += take * px
        remaining -= take
        if remaining <= 0:
            break
    if remaining > 0:
        # Not enough depth on visible levels — pay the worst level for the rest
        cost += remaining * levels[-1][0]
    avg = cost / qty
    return market_fill_price(side_buy, avg, extra_slippage_bps)
