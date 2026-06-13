"""Pure martingale ladder math — no I/O, no state, fully Decimal (trading §1.2).

A "grid" is the set of order legs for one DCA cycle anchored at a base price:
the immediate base order (index 0) plus ``max_safety_orders`` safety orders at
progressively lower prices and larger sizes. Everything here is a deterministic
function of (base_price, params) so it is trivially unit-testable.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from .config import MartingaleParams


@dataclass(frozen=True)
class Leg:
    """One order leg of a martingale cycle."""

    index: int            # 0 = base order, 1..N = safety order number
    is_base: bool
    deviation_pct: Decimal  # cumulative drop from base price (>= 0; 0 for base)
    price: Decimal          # trigger/limit price for this leg
    quote_size: Decimal     # USDT committed at this leg
    base_qty: Decimal       # base asset acquired = quote_size / price (no fees/slippage)


@dataclass(frozen=True)
class Grid:
    """The full ladder for one cycle, base leg first."""

    base_price: Decimal
    legs: tuple[Leg, ...]

    @property
    def safety_legs(self) -> tuple[Leg, ...]:
        return self.legs[1:]

    @property
    def total_quote(self) -> Decimal:
        """USDT committed if every leg fills."""
        return sum((leg.quote_size for leg in self.legs), Decimal("0"))

    @property
    def total_base_qty(self) -> Decimal:
        """Base asset held if every leg fills (ignoring fees/slippage)."""
        return sum((leg.base_qty for leg in self.legs), Decimal("0"))


def build_grid(base_price: Decimal, params: MartingaleParams) -> Grid:
    """Construct the ladder anchored at ``base_price``.

    Safety order k deviation = sum of geometric steps
        step_1 = price_drop_step
        step_{k} = step_{k-1} * step_scale
    Safety order k size = safety_order_size * volume_scale^(k-1).
    """
    if base_price <= 0:
        raise ValueError(f"base_price must be > 0, got {base_price}")

    legs: list[Leg] = [
        Leg(
            index=0,
            is_base=True,
            deviation_pct=Decimal("0"),
            price=base_price,
            quote_size=params.base_order_size,
            base_qty=params.base_order_size / base_price,
        )
    ]

    cum_dev = Decimal("0")
    step = params.price_drop_step
    for i in range(1, params.max_safety_orders + 1):
        cum_dev += step
        price = base_price * (Decimal("1") - cum_dev)
        if price <= 0:
            # Guarded against at config time (_deepest_leg_stays_positive); belt+braces.
            raise ValueError(
                f"safety order {i} price {price} <= 0 (cumulative deviation {cum_dev})"
            )
        quote = params.safety_order_size * (params.volume_scale ** (i - 1))
        legs.append(
            Leg(
                index=i,
                is_base=False,
                deviation_pct=cum_dev,
                price=price,
                quote_size=quote,
                base_qty=quote / price,
            )
        )
        step *= params.step_scale

    return Grid(base_price=base_price, legs=tuple(legs))


def average_entry(filled: Sequence[Leg]) -> Decimal:
    """Quantity-weighted average fill price over the filled legs.

    avg = total_quote / total_base  (the price at which the blended position sits).
    """
    if not filled:
        raise ValueError("average_entry requires at least one filled leg")
    total_quote = sum((leg.quote_size for leg in filled), Decimal("0"))
    total_base = sum((leg.base_qty for leg in filled), Decimal("0"))
    if total_base <= 0:
        raise ValueError("total base quantity must be > 0")
    return total_quote / total_base


def tp_price(avg_entry: Decimal, tp_target: Decimal) -> Decimal:
    """Take-profit trigger price = avg_entry * (1 + tp_target)."""
    return avg_entry * (Decimal("1") + tp_target)
