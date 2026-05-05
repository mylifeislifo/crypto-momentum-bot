"""ATR-based position sizing with concentration cap."""
from __future__ import annotations

from decimal import Decimal


def position_qty(
    equity: Decimal,
    risk_per_trade: float,
    entry_price: Decimal,
    atr_value: Decimal,
    atr_stop_mult: float,
    max_concentration: float,
    min_notional: Decimal,
    lot_size: Decimal,
) -> Decimal:
    """Return quantity to buy (long-only). Returns 0 if not viable.

    Risk-based qty = (equity * risk%) / stop_distance
    Capped to (equity * max_concentration) / entry_price.
    Floored to multiples of lot_size; rejected if notional < min_notional.
    """
    if entry_price <= 0 or atr_value <= 0:
        return Decimal(0)

    stop_dist = atr_value * Decimal(str(atr_stop_mult))
    if stop_dist <= 0:
        return Decimal(0)

    risk_qty = (equity * Decimal(str(risk_per_trade))) / stop_dist
    cap_qty = (equity * Decimal(str(max_concentration))) / entry_price
    qty = min(risk_qty, cap_qty)

    # round down to lot_size
    if lot_size > 0:
        steps = (qty / lot_size).to_integral_value(rounding="ROUND_FLOOR")
        qty = steps * lot_size

    if qty <= 0:
        return Decimal(0)
    if qty * entry_price < min_notional:
        return Decimal(0)
    return qty


def initial_stop(entry_price: Decimal, atr_value: Decimal, mult: float) -> Decimal:
    return entry_price - atr_value * Decimal(str(mult))
