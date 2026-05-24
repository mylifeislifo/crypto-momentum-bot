"""Trailing stop state machine.

One TrailState per open position. Updated on each new Bar (not every OB tick)
to avoid flooding the exchange with SL amendment requests.

Trail logic:
  LONG : trail_stop = peak_high − ATR × multiplier  (monotonically increases)
  SHORT: trail_stop = trough_low + ATR × multiplier  (monotonically decreases)

The trail manager ONLY raises/lowers the stop. The actual SL order amendment
is handled by execution/order_manager.py, which receives TrailUpdate objects
from on_new_bar().
"""

import structlog
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from ..core.clock import utc_now
from ..core.enums import Interval, Side
from ..core.types import Bar, TrailUpdate

logger = structlog.get_logger(__name__)


def compute_atr(bars: list[Bar], period: int = 5) -> Decimal:
    """Simple SMA-ATR from OHLC bars. Returns Decimal('0') if < 2 bars."""
    if len(bars) < 2:
        return Decimal("0")

    trs: list[float] = []
    for i in range(1, len(bars)):
        h = float(bars[i].high)
        lo = float(bars[i].low)
        pc = float(bars[i - 1].close)
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))

    recent = trs[-period:]
    return Decimal(str(sum(recent) / len(recent)))


@dataclass
class TrailState:
    position_id: str
    side: Side
    sl_order_id: str
    current_stop: Decimal
    peak_price: Decimal     # LONG: highest high seen; SHORT: lowest low seen
    atr: Decimal


class TrailingStopManager:
    def __init__(self, atr_multiplier: float, atr_period: int = 5) -> None:
        self._multiplier = Decimal(str(atr_multiplier))
        self._atr_period = atr_period
        self._positions: dict[str, TrailState] = {}
        self._bar_history_5m: list[Bar] = []

    def register(
        self,
        position_id: str,
        side: Side,
        sl_order_id: str,
        initial_stop: Decimal,
        entry_price: Decimal,
        atr: Decimal,
    ) -> None:
        # fallback ATR if not yet available: 0.5% of entry price
        effective_atr = atr if atr > 0 else entry_price * Decimal("0.005")
        self._positions[position_id] = TrailState(
            position_id=position_id,
            side=side,
            sl_order_id=sl_order_id,
            current_stop=initial_stop,
            peak_price=entry_price,
            atr=effective_atr,
        )
        logger.info(
            "trail.registered",
            position_id=position_id,
            side=side.value,
            initial_stop=str(initial_stop),
            atr=str(effective_atr),
        )

    def on_new_bar(self, bar: Bar) -> list[TrailUpdate]:
        """Call on every new Bar. Returns SL amendments to execute."""
        if bar.interval == Interval.M5:
            self._bar_history_5m.append(bar)
            if len(self._bar_history_5m) > 50:
                self._bar_history_5m = self._bar_history_5m[-50:]

            new_atr = compute_atr(self._bar_history_5m, self._atr_period)
            if new_atr > 0:
                for state in self._positions.values():
                    state.atr = new_atr

        updates: list[TrailUpdate] = []
        for state in self._positions.values():
            update = self._tick(state, bar)
            if update is not None:
                updates.append(update)
        return updates

    def update_sl_order_id(self, position_id: str, new_sl_order_id: str) -> None:
        """Called by order_manager after a successful SL amendment."""
        if state := self._positions.get(position_id):
            state.sl_order_id = new_sl_order_id

    def on_close(self, position_id: str) -> None:
        self._positions.pop(position_id, None)

    def get_current_stop(self, position_id: str) -> Optional[Decimal]:
        state = self._positions.get(position_id)
        return state.current_stop if state else None

    def active_position_ids(self) -> list[str]:
        return list(self._positions.keys())

    def _tick(self, state: TrailState, bar: Bar) -> Optional[TrailUpdate]:
        trail_offset = state.atr * self._multiplier

        if state.side == Side.LONG:
            if bar.high > state.peak_price:
                state.peak_price = bar.high

            new_stop = (state.peak_price - trail_offset).quantize(Decimal("0.01"))

            # monotonically increasing: never lower the stop for a long
            if new_stop > state.current_stop:
                return self._emit_update(state, new_stop)

        elif state.side == Side.SHORT:
            if bar.low < state.peak_price:
                state.peak_price = bar.low

            new_stop = (state.peak_price + trail_offset).quantize(Decimal("0.01"))

            # monotonically decreasing: never raise the stop for a short
            if new_stop < state.current_stop:
                return self._emit_update(state, new_stop)

        return None

    @staticmethod
    def _emit_update(state: TrailState, new_stop: Decimal) -> TrailUpdate:
        old_stop = state.current_stop
        state.current_stop = new_stop
        logger.info(
            "trail.stop_moved",
            position_id=state.position_id,
            side=state.side.value,
            old_stop=str(old_stop),
            new_stop=str(new_stop),
        )
        return TrailUpdate(
            position_id=state.position_id,
            old_sl_order_id=state.sl_order_id,
            new_stop_price=new_stop,
            old_stop_price=old_stop,
            ts=utc_now(),
        )
