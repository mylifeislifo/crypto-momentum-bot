"""Position sizer: Fixed Fractional method using SL distance.

Formula:
    risk_amount = equity × risk_per_trade
    stop_dist   = |entry_price − stop_price|
    qty         = risk_amount / stop_dist
    qty         = min(qty, max_leverage × equity / entry_price)  [leverage cap]
    qty         = floor(qty / lot_size) × lot_size               [lot rounding]

Returns Decimal("0") if the position would be too small (below min_notional)
or if inputs are invalid. Callers must reject zero-qty signals.
"""

from decimal import ROUND_DOWN, Decimal

_LOT_SIZE = Decimal("0.001")        # Binance BTCUSDT lot size
_MIN_NOTIONAL = Decimal("100")      # Binance min order notional (USDT)


def compute_qty(
    entry_price: Decimal,
    stop_price: Decimal,
    equity: Decimal,
    risk_per_trade: float = 0.01,
    max_leverage: int = 2,
    lot_size: Decimal = _LOT_SIZE,
    min_notional: Decimal = _MIN_NOTIONAL,
) -> Decimal:
    if equity <= 0 or entry_price <= 0:
        return Decimal("0")

    stop_dist = abs(entry_price - stop_price)
    if stop_dist == Decimal("0"):
        return Decimal("0")

    risk_amount = equity * Decimal(str(risk_per_trade))
    qty = risk_amount / stop_dist

    # leverage headroom cap: never exceed max_leverage × equity in notional
    max_qty = (equity * Decimal(str(max_leverage))) / entry_price
    qty = min(qty, max_qty)

    # round down to exchange lot size
    qty = (qty / lot_size).to_integral_value(rounding=ROUND_DOWN) * lot_size

    # enforce minimum notional
    if qty * entry_price < min_notional or qty < lot_size:
        return Decimal("0")

    return qty
