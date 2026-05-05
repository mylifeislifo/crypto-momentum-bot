"""Stop management: initial ATR stop, chandelier trail, time stop."""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum

from bot.core.types import Position


class StopReason(str, Enum):
    NONE = "none"
    INITIAL_STOP = "initial_stop"
    TRAIL_STOP = "trail_stop"
    TIME_STOP = "time_stop"


def update_trail(pos: Position, atr_value: Decimal, trail_mult: float) -> Decimal:
    """Chandelier exit: high_watermark - trail_mult * ATR. Monotonically non-decreasing."""
    candidate = pos.high_watermark - atr_value * Decimal(str(trail_mult))
    if candidate > pos.trail_stop:
        pos.trail_stop = candidate
    return pos.trail_stop


def check_stops(
    pos: Position,
    bar_low: Decimal,
    bar_close: Decimal,
    now: datetime,
    time_stop_hours: float,
) -> StopReason:
    """Return the first stop reason that triggers, else NONE.

    Order of precedence: initial > trail > time.
    Long-only: stop triggers when low <= stop level (intrabar) or close <= stop.
    """
    # Initial stop is set at entry; if trail hasn't moved it up, both are equal.
    if pos.initial_stop > 0 and bar_low <= pos.initial_stop and pos.trail_stop <= pos.initial_stop:
        return StopReason.INITIAL_STOP
    if pos.trail_stop > 0 and bar_low <= pos.trail_stop:
        return StopReason.TRAIL_STOP
    if (now - pos.entry_ts) >= timedelta(hours=time_stop_hours):
        # 1R = entry - initial_stop. Check that we never made +1R progress.
        one_r = pos.entry_price - pos.initial_stop
        if pos.high_watermark < pos.entry_price + one_r:
            return StopReason.TIME_STOP
    return StopReason.NONE
