"""Pure-function spoofing detection for L2 orderbook levels.

A level is flagged as a probable spoof if its quantity exceeds
`size_multiplier` times the mean of its neighboring levels on the same side.
Flagged levels are excluded from the imbalance calculation.

This is a stateless size filter. Temporal detection (tracking whether a level
disappears within N snapshots) is handled in pipeline_orderbook.py, which has
access to snapshot history.
"""

from decimal import Decimal

from ..core.types import OBLevel


def filter_spoofs(
    levels: list[OBLevel],
    size_multiplier: float = 3.0,
    neighbor_window: int = 2,
) -> list[OBLevel]:
    """Return levels with probable spoofs removed.

    Args:
        levels: Ordered list of OBLevel (bids desc, asks asc).
        size_multiplier: Flag a level if qty > multiplier × neighbor mean.
        neighbor_window: How many levels on each side to use as neighbors.

    Returns:
        Filtered list (same order, flagged levels omitted).
    """
    if len(levels) <= neighbor_window * 2:
        return levels  # too few levels to compute meaningful neighbors

    result: list[OBLevel] = []
    qtys = [float(lvl.qty) for lvl in levels]

    for i, level in enumerate(levels):
        lo = max(0, i - neighbor_window)
        hi = min(len(levels), i + neighbor_window + 1)
        neighbor_qtys = qtys[lo:i] + qtys[i + 1:hi]

        if not neighbor_qtys:
            result.append(level)
            continue

        neighbor_mean = sum(neighbor_qtys) / len(neighbor_qtys)
        if neighbor_mean == 0 or float(level.qty) <= size_multiplier * neighbor_mean:
            result.append(level)
        # else: flagged as spoof, drop

    return result


def compute_imbalance(bids: list[OBLevel], asks: list[OBLevel]) -> float:
    """Compute (bid_vol - ask_vol) / (bid_vol + ask_vol).

    Returns 0.0 if both sides are empty. Range: [-1.0, +1.0].
    Positive = bid-heavy (bullish pressure), negative = ask-heavy.
    """
    bid_vol = sum(float(lvl.qty) for lvl in bids)
    ask_vol = sum(float(lvl.qty) for lvl in asks)
    total = bid_vol + ask_vol
    if total == 0.0:
        return 0.0
    return (bid_vol - ask_vol) / total
