"""Trailing stop state machine.

One TrailState per open position. Updated on each new Bar (not every OB tick)
to avoid flooding the exchange with SL amendment requests.

Trail logic:
  LONG : trail_stop = peak_high − ATR × multiplier  (monotonically increases)
  SHORT: trail_stop = trough_low + ATR × multiplier  (monotonically decreases)

Breakeven floor (winner-asymmetry, trading §3):
  Once price moves +breakeven_trigger_pct in the favorable direction, the stop
  is never allowed below entry again (LONG) / above entry (SHORT). A winner can
  therefore not round-trip into a loss — the exit-side embodiment of "alpha is
  in the exit" (R5). The ATR trail still takes over once it rises past the
  breakeven level. Disabled when breakeven_trigger_pct == 0.

The trail manager ONLY raises/lowers the stop. The actual SL order amendment
is handled by execution/order_manager.py, which receives TrailUpdate objects
from on_new_bar().
"""

import json
import structlog
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Optional

from ..core.clock import utc_now
from ..core.enums import Interval, Side
from ..core.types import Bar, ForcedExit, TrailUpdate

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
    entry_price: Decimal
    be_armed: bool = False  # True once the +trigger% breakeven floor has engaged
    bars_held: int = 0      # number of 5m bars since entry (for the time stop)


class TrailingStopManager:
    def __init__(
        self,
        atr_multiplier: float,
        atr_period: int = 5,
        breakeven_trigger_pct: float = 0.0,
        breakeven_offset_pct: float = 0.0,
        time_stop_bars: int = 0,
        max_hold_bars: int = 0,
        state_file: Optional[Path] = None,
    ) -> None:
        self._multiplier = Decimal(str(atr_multiplier))
        self._atr_period = atr_period
        self._be_trigger = Decimal(str(breakeven_trigger_pct))   # 0 disables breakeven
        self._be_offset = Decimal(str(breakeven_offset_pct))
        self._time_stop_bars = time_stop_bars   # 0 disables conditional time stop
        self._max_hold_bars = max_hold_bars     # 0 disables hard cap
        self._state_file = state_file           # None disables persistence
        self._positions: dict[str, TrailState] = {}
        self._bar_history_5m: list[Bar] = []
        self.load_state()

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
            entry_price=entry_price,
        )
        logger.info(
            "trail.registered",
            position_id=position_id,
            side=side.value,
            initial_stop=str(initial_stop),
            atr=str(effective_atr),
        )
        self._persist()

    def on_new_bar(self, bar: Bar) -> list[TrailUpdate]:
        """Call on every new Bar. Returns SL amendments to execute."""
        if bar.interval == Interval.M5:
            self._bar_history_5m.append(bar)
            if len(self._bar_history_5m) > 50:
                self._bar_history_5m = self._bar_history_5m[-50:]

            new_atr = compute_atr(self._bar_history_5m, self._atr_period)
            # one 5m bar elapsed → age every open position (drives the time stop)
            for state in self._positions.values():
                state.bars_held += 1
                if new_atr > 0:
                    state.atr = new_atr

        updates: list[TrailUpdate] = []
        for state in self._positions.values():
            update = self._tick(state, bar)
            if update is not None:
                updates.append(update)
        if bar.interval == Interval.M5:
            self._persist()   # bars_held / peak / atr / stop changed this bar
        return updates

    def update_sl_order_id(self, position_id: str, new_sl_order_id: str) -> None:
        """Called by order_manager after a successful SL amendment."""
        if state := self._positions.get(position_id):
            state.sl_order_id = new_sl_order_id
            self._persist()

    def on_close(self, position_id: str) -> None:
        if self._positions.pop(position_id, None) is not None:
            self._persist()

    def get_current_stop(self, position_id: str) -> Optional[Decimal]:
        state = self._positions.get(position_id)
        return state.current_stop if state else None

    def current_atr(self) -> Decimal:
        """Latest ATR from the 5m bar history (Decimal('0') until >=2 bars seen).
        Used to seed a NEW position's initial trail offset from real price range
        rather than a stale/garbage value."""
        return compute_atr(self._bar_history_5m, self._atr_period)

    # ------------------------------------------------------------------
    # Persistence — survive restarts so winners keep running across the
    # bot's scheduled restart cycle (peak / be_armed / bars_held are state
    # the time stop and breakeven depend on; losing them would re-arm the
    # time-stop clock and cut proven winners). order_manager.recover_positions
    # reconciles these against the gateway's positions on startup.
    # ------------------------------------------------------------------

    def _persist(self) -> None:
        if self._state_file is None:
            return
        try:
            data = {
                pid: {
                    "side": s.side.value,
                    "sl_order_id": s.sl_order_id,
                    "current_stop": str(s.current_stop),
                    "peak_price": str(s.peak_price),
                    "atr": str(s.atr),
                    "entry_price": str(s.entry_price),
                    "be_armed": s.be_armed,
                    "bars_held": s.bars_held,
                }
                for pid, s in self._positions.items()
            }
            self._state_file.write_text(json.dumps(data, indent=2))
        except Exception as exc:
            logger.warning("trail.persist_failed", error=str(exc))

    def load_state(self) -> None:
        if self._state_file is None or not self._state_file.exists():
            return
        try:
            data = json.loads(self._state_file.read_text())
            for pid, d in data.items():
                self._positions[pid] = TrailState(
                    position_id=pid,
                    side=Side(d["side"]),
                    sl_order_id=d["sl_order_id"],
                    current_stop=Decimal(d["current_stop"]),
                    peak_price=Decimal(d["peak_price"]),
                    atr=Decimal(d["atr"]),
                    entry_price=Decimal(d["entry_price"]),
                    be_armed=bool(d["be_armed"]),
                    bars_held=int(d["bars_held"]),
                )
            logger.info("trail.state_loaded", positions=len(self._positions))
        except Exception as exc:
            logger.warning("trail.state_load_failed", error=str(exc))

    def active_position_ids(self) -> list[str]:
        return list(self._positions.keys())

    def due_time_exits(self) -> list[ForcedExit]:
        """Positions the time stop wants market-closed as of the current bar.

        Pure read (no mutation): the caller closes each and calls on_close, which
        removes it so it is not reported again (and a failed close is retried next
        bar). Two rules, hard cap first:
          • max_hold  — any position older than max_hold_bars (hard cap).
          • time_stop — an UNPROVEN position (breakeven never armed = it never
            printed +trigger% favorable) older than time_stop_bars. Proven winners
            (be_armed) are exempt and keep riding the trail — winner-asymmetry.
        """
        exits: list[ForcedExit] = []
        for state in self._positions.values():
            reason: Optional[str] = None
            if self._max_hold_bars > 0 and state.bars_held >= self._max_hold_bars:
                reason = "max_hold"
            elif (
                self._time_stop_bars > 0
                and not state.be_armed
                and state.bars_held >= self._time_stop_bars
            ):
                reason = "time_stop"
            if reason is not None:
                exits.append(
                    ForcedExit(
                        position_id=state.position_id,
                        side=state.side,
                        reason=reason,
                        bars_held=state.bars_held,
                        ts=utc_now(),
                    )
                )
        return exits

    def _tick(self, state: TrailState, bar: Bar) -> Optional[TrailUpdate]:
        trail_offset = state.atr * self._multiplier

        if state.side == Side.LONG:
            if bar.high > state.peak_price:
                state.peak_price = bar.high

            new_stop = (state.peak_price - trail_offset).quantize(Decimal("0.01"))

            # breakeven floor: once +trigger% in profit, the stop never drops below
            # entry again — a winner cannot round-trip to a loss (winner-asymmetry).
            if self._be_trigger > 0:
                self._maybe_arm_breakeven(state)
                if state.be_armed:
                    be_floor = (
                        state.entry_price * (Decimal("1") + self._be_offset)
                    ).quantize(Decimal("0.01"))
                    if be_floor > new_stop:
                        new_stop = be_floor

            # monotonically increasing: never lower the stop for a long
            if new_stop > state.current_stop:
                return self._emit_update(state, new_stop)

        elif state.side == Side.SHORT:
            if bar.low < state.peak_price:
                state.peak_price = bar.low

            new_stop = (state.peak_price + trail_offset).quantize(Decimal("0.01"))

            # breakeven ceiling (mirror of the long floor)
            if self._be_trigger > 0:
                self._maybe_arm_breakeven(state)
                if state.be_armed:
                    be_ceil = (
                        state.entry_price * (Decimal("1") - self._be_offset)
                    ).quantize(Decimal("0.01"))
                    if be_ceil < new_stop:
                        new_stop = be_ceil

            # monotonically decreasing: never raise the stop for a short
            if new_stop < state.current_stop:
                return self._emit_update(state, new_stop)

        return None

    def _maybe_arm_breakeven(self, state: TrailState) -> None:
        """Arm the breakeven floor the first time peak excursion reaches +trigger%.

        Uses peak_price (max favorable excursion), so arming is based only on the
        best price seen so far — once a real +trigger% move printed, the resting
        stop is moved to (around) entry. No look-ahead: in live trading the
        exchange SL fills on the subsequent adverse move, not this bar."""
        if state.be_armed:
            return
        if state.side == Side.LONG:
            reached = state.peak_price >= state.entry_price * (Decimal("1") + self._be_trigger)
        else:
            reached = state.peak_price <= state.entry_price * (Decimal("1") - self._be_trigger)
        if reached:
            state.be_armed = True
            logger.info(
                "trail.breakeven_armed",
                position_id=state.position_id,
                side=state.side.value,
                entry=str(state.entry_price),
                peak=str(state.peak_price),
            )

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
