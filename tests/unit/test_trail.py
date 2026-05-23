"""Tests for TrailingStopManager.

Key invariants:
  LONG  → trail_stop is monotonically increasing (never decreases)
  SHORT → trail_stop is monotonically decreasing (never increases)
  TrailUpdate is emitted only when stop actually moves
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from bot.core.enums import Interval, Side
from bot.core.types import Bar
from bot.risk.trail import TrailingStopManager, compute_atr

_TS = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _bar(high: float, low: float, close: float, open_: float = None, cvd: float = 0.0) -> Bar:
    o = Decimal(str(open_ if open_ is not None else close))
    return Bar(
        ts=_TS,
        interval=Interval.M5,
        open=o,
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(close)),
        volume=Decimal("10"),
        buy_volume=Decimal("6"),
        sell_volume=Decimal("4"),
        cvd_delta=cvd,
        cvd_cumulative=cvd,
        vwap=Decimal(str(close)),
        trade_count=100,
    )


def _manager(multiplier: float = 1.5) -> TrailingStopManager:
    return TrailingStopManager(atr_multiplier=multiplier, atr_period=3)


# ---------------------------------------------------------------------------
# ATR computation
# ---------------------------------------------------------------------------

class TestComputeAtr:
    def test_basic_atr(self):
        bars = [
            _bar(high=100, low=90, close=95),   # TR = 10
            _bar(high=105, low=98, close=102),  # TR = max(7, |105-95|, |98-95|) = 10
            _bar(high=108, low=100, close=104), # TR = max(8, |108-102|, |100-102|) = 8
        ]
        atr = compute_atr(bars, period=2)
        # Last 2 TRs: 10, 8 → average = 9
        assert float(atr) == pytest.approx(9.0)

    def test_returns_zero_on_single_bar(self):
        assert compute_atr([_bar(100, 90, 95)]) == Decimal("0")

    def test_returns_zero_on_empty(self):
        assert compute_atr([]) == Decimal("0")


# ---------------------------------------------------------------------------
# LONG trailing stop: monotonically increasing
# ---------------------------------------------------------------------------

class TestLongTrail:
    def _setup(self, initial_stop: Decimal, entry: Decimal, atr: Decimal) -> TrailingStopManager:
        mgr = _manager(multiplier=1.5)
        mgr.register("pos1", Side.LONG, "sl_order_1", initial_stop, entry, atr)
        return mgr

    def test_stop_moves_up_as_price_rises(self):
        entry = Decimal("50000")
        atr = Decimal("200")
        initial_stop = entry - atr * Decimal("1.5")  # = 49700
        mgr = self._setup(initial_stop, entry, atr)

        # price rises to 51000: peak = 51000, new_stop = 51000 - 300 = 50700
        updates = mgr.on_new_bar(_bar(high=51000, low=50500, close=50800))
        assert len(updates) == 1
        assert updates[0].new_stop_price > initial_stop
        assert updates[0].new_stop_price == Decimal("50700.00")

    def test_stop_does_not_decrease_on_price_pullback(self):
        entry = Decimal("50000")
        atr = Decimal("200")
        initial_stop = entry - atr * Decimal("1.5")
        mgr = self._setup(initial_stop, entry, atr)

        # price rises
        mgr.on_new_bar(_bar(high=51000, low=50500, close=50800))
        stop_after_rise = mgr.get_current_stop("pos1")

        # price pulls back but does NOT go below stop → stop should not move
        updates = mgr.on_new_bar(_bar(high=50700, low=50200, close=50400))
        # no new high → no TrailUpdate (stop stays same)
        assert len(updates) == 0
        assert mgr.get_current_stop("pos1") == stop_after_rise

    def test_stop_monotonically_increasing_over_multiple_bars(self):
        entry = Decimal("50000")
        atr = Decimal("200")
        initial_stop = entry - atr * Decimal("1.5")
        mgr = self._setup(initial_stop, entry, atr)

        stops = [initial_stop]
        for high in [50500, 51000, 51500, 51200, 51800, 51600]:
            mgr.on_new_bar(_bar(high=high, low=high - 500, close=high - 200))
            stops.append(mgr.get_current_stop("pos1"))

        # each stop >= previous (monotonically non-decreasing)
        for i in range(1, len(stops)):
            assert stops[i] >= stops[i - 1]

    def test_no_update_when_price_flat(self):
        entry = Decimal("50000")
        atr = Decimal("200")
        initial_stop = entry - atr * Decimal("1.5")
        mgr = self._setup(initial_stop, entry, atr)

        # same price as entry → no new peak → no update
        updates = mgr.on_new_bar(_bar(high=50000, low=49800, close=49900))
        assert len(updates) == 0


# ---------------------------------------------------------------------------
# SHORT trailing stop: monotonically decreasing
# ---------------------------------------------------------------------------

class TestShortTrail:
    def _setup(self, initial_stop: Decimal, entry: Decimal, atr: Decimal) -> TrailingStopManager:
        mgr = _manager(multiplier=1.5)
        mgr.register("pos1", Side.SHORT, "sl_order_1", initial_stop, entry, atr)
        return mgr

    def test_stop_moves_down_as_price_falls(self):
        entry = Decimal("50000")
        atr = Decimal("200")
        initial_stop = entry + atr * Decimal("1.5")  # = 50300
        mgr = self._setup(initial_stop, entry, atr)

        # price falls to 49000: trough = 49000, new_stop = 49000 + 300 = 49300
        updates = mgr.on_new_bar(_bar(high=49500, low=49000, close=49200))
        assert len(updates) == 1
        assert updates[0].new_stop_price < initial_stop
        assert updates[0].new_stop_price == Decimal("49300.00")

    def test_stop_does_not_increase_on_price_bounce(self):
        entry = Decimal("50000")
        atr = Decimal("200")
        initial_stop = entry + atr * Decimal("1.5")
        mgr = self._setup(initial_stop, entry, atr)

        # price falls
        mgr.on_new_bar(_bar(high=49500, low=49000, close=49200))
        stop_after_fall = mgr.get_current_stop("pos1")

        # price bounces up → stop must NOT rise
        updates = mgr.on_new_bar(_bar(high=49800, low=49400, close=49600))
        assert len(updates) == 0
        assert mgr.get_current_stop("pos1") == stop_after_fall

    def test_stop_monotonically_decreasing_over_multiple_bars(self):
        entry = Decimal("50000")
        atr = Decimal("200")
        initial_stop = entry + atr * Decimal("1.5")
        mgr = self._setup(initial_stop, entry, atr)

        stops = [initial_stop]
        for low in [49500, 49000, 48500, 48800, 48200, 48400]:
            mgr.on_new_bar(_bar(high=low + 400, low=low, close=low + 200))
            stops.append(mgr.get_current_stop("pos1"))

        for i in range(1, len(stops)):
            assert stops[i] <= stops[i - 1]


# ---------------------------------------------------------------------------
# Multi-position management
# ---------------------------------------------------------------------------

class TestMultiPosition:
    def test_multiple_positions_tracked_independently(self):
        mgr = _manager(multiplier=1.5)
        atr = Decimal("200")

        mgr.register("long1", Side.LONG, "sl_1", Decimal("49700"), Decimal("50000"), atr)
        mgr.register("short1", Side.SHORT, "sl_2", Decimal("50300"), Decimal("50000"), atr)

        # price rises: long stop should move up, short stop should not move
        updates = mgr.on_new_bar(_bar(high=51000, low=50400, close=50800))
        long_updates = [u for u in updates if u.position_id == "long1"]
        short_updates = [u for u in updates if u.position_id == "short1"]

        assert len(long_updates) == 1
        assert len(short_updates) == 0

    def test_on_close_removes_position(self):
        mgr = _manager()
        atr = Decimal("200")
        mgr.register("pos1", Side.LONG, "sl_1", Decimal("49700"), Decimal("50000"), atr)
        assert "pos1" in mgr.active_position_ids()

        mgr.on_close("pos1")
        assert "pos1" not in mgr.active_position_ids()

        # no updates after close
        updates = mgr.on_new_bar(_bar(high=52000, low=51000, close=51500))
        assert not any(u.position_id == "pos1" for u in updates)

    def test_update_sl_order_id(self):
        mgr = _manager()
        mgr.register("pos1", Side.LONG, "old_sl", Decimal("49700"), Decimal("50000"), Decimal("200"))
        mgr.update_sl_order_id("pos1", "new_sl")
        assert mgr._positions["pos1"].sl_order_id == "new_sl"


# ---------------------------------------------------------------------------
# TrailUpdate fields
# ---------------------------------------------------------------------------

class TestTrailUpdateFields:
    def test_update_contains_correct_old_and_new_stops(self):
        mgr = _manager(multiplier=1.5)
        atr = Decimal("200")
        initial_stop = Decimal("49700")
        mgr.register("pos1", Side.LONG, "sl_1", initial_stop, Decimal("50000"), atr)

        updates = mgr.on_new_bar(_bar(high=51000, low=50500, close=50800))
        assert len(updates) == 1
        u = updates[0]
        assert u.old_stop_price == initial_stop
        assert u.new_stop_price > initial_stop
        assert u.old_sl_order_id == "sl_1"
        assert u.position_id == "pos1"
