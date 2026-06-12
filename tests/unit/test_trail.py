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


def _bar(high: float, low: float, close: float, open_: float = None, cvd: float = 0.0,
         interval: Interval = Interval.M5) -> Bar:
    o = Decimal(str(open_ if open_ is not None else close))
    return Bar(
        ts=_TS,
        interval=interval,
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


def test_current_atr_reflects_bar_history():
    # F3: the ATR used to seed a new position must come from real price range
    mgr = _manager(multiplier=1.5)
    assert mgr.current_atr() == Decimal("0")            # no bars yet
    mgr.on_new_bar(_bar(high=100, low=90, close=95))    # 1 bar → still 0 (needs >=2)
    assert mgr.current_atr() == Decimal("0")
    mgr.on_new_bar(_bar(high=105, low=98, close=102))   # 2 bars → real ATR
    assert mgr.current_atr() > Decimal("0")


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


# ---------------------------------------------------------------------------
# Breakeven floor (winner-asymmetry, trading §3): once +trigger% in profit the
# stop never drops below entry — a winner cannot round-trip to a loss.
# ---------------------------------------------------------------------------

def _be_manager(multiplier: float = 1.5, trigger: float = 0.01, offset: float = 0.0) -> TrailingStopManager:
    return TrailingStopManager(
        atr_multiplier=multiplier, atr_period=3,
        breakeven_trigger_pct=trigger, breakeven_offset_pct=offset,
    )


class TestBreakeven:
    def test_long_floors_stop_to_entry_when_atr_trail_below_entry(self):
        # large ATR (600 → offset 900) keeps the pure ATR trail BELOW entry,
        # isolating the breakeven floor. Single bar → ATR stays the registered 600.
        mgr = _be_manager(trigger=0.01)
        mgr.register("p", Side.LONG, "sl", Decimal("49100"), Decimal("50000"), Decimal("600"))
        updates = mgr.on_new_bar(_bar(high=50500, low=50050, close=50100))  # +1% high
        assert len(updates) == 1
        # ATR trail = 50500 - 900 = 49600 (below entry); BE floors it to entry 50000
        assert updates[0].new_stop_price == Decimal("50000.00")

    def test_long_not_armed_below_trigger(self):
        mgr = _be_manager(trigger=0.01)
        mgr.register("p", Side.LONG, "sl", Decimal("49100"), Decimal("50000"), Decimal("600"))
        updates = mgr.on_new_bar(_bar(high=50200, low=50050, close=50100))  # only +0.4%
        assert len(updates) == 1
        # below trigger → no BE floor → pure ATR trail 50200 - 900 = 49300
        assert updates[0].new_stop_price == Decimal("49300.00")

    def test_long_trail_takes_over_above_breakeven(self):
        mgr = _be_manager(trigger=0.01)
        mgr.register("p", Side.LONG, "sl", Decimal("49100"), Decimal("50000"), Decimal("600"))
        u1 = mgr.on_new_bar(_bar(high=50500, low=50500, close=50500))  # arms BE → floor 50000
        assert u1[0].new_stop_price == Decimal("50000.00")
        u2 = mgr.on_new_bar(_bar(high=51000, low=51000, close=51000))  # higher → ATR trail rises
        assert len(u2) == 1
        assert u2[0].new_stop_price > Decimal("50000.00")  # trail now sits above breakeven

    def test_short_caps_stop_to_entry(self):
        mgr = _be_manager(trigger=0.01)
        mgr.register("p", Side.SHORT, "sl", Decimal("50900"), Decimal("50000"), Decimal("600"))
        updates = mgr.on_new_bar(_bar(high=49950, low=49500, close=49900))  # -1% low
        assert len(updates) == 1
        # ATR trail = 49500 + 900 = 50400 (above entry); BE caps it to entry 50000
        assert updates[0].new_stop_price == Decimal("50000.00")

    def test_disabled_when_trigger_zero(self):
        mgr = _be_manager(trigger=0.0)  # breakeven off → legacy pure-ATR behavior
        mgr.register("p", Side.LONG, "sl", Decimal("49100"), Decimal("50000"), Decimal("600"))
        updates = mgr.on_new_bar(_bar(high=50500, low=50500, close=50500))
        assert updates[0].new_stop_price == Decimal("49600.00")  # no floor to entry

    def test_offset_lifts_breakeven_above_entry_to_cover_fees(self):
        mgr = _be_manager(trigger=0.01, offset=0.001)  # BE floor = entry +0.1%
        mgr.register("p", Side.LONG, "sl", Decimal("49100"), Decimal("50000"), Decimal("600"))
        updates = mgr.on_new_bar(_bar(high=50500, low=50050, close=50100))
        assert len(updates) == 1
        assert updates[0].new_stop_price == Decimal("50050.00")  # 50000 * 1.001

    def test_breakeven_holds_when_price_pulls_back_to_entry(self):
        # once armed, a pullback toward entry must NOT lower the stop below breakeven
        mgr = _be_manager(trigger=0.01)
        mgr.register("p", Side.LONG, "sl", Decimal("49100"), Decimal("50000"), Decimal("600"))
        mgr.on_new_bar(_bar(high=50500, low=50500, close=50500))  # arms BE → floor 50000
        assert mgr.get_current_stop("p") == Decimal("50000.00")
        updates = mgr.on_new_bar(_bar(high=50000, low=49900, close=49950))  # falls back
        assert updates == []                                  # no lowering emitted
        assert mgr.get_current_stop("p") == Decimal("50000.00")  # held at breakeven


# ---------------------------------------------------------------------------
# Time stop (winner-asymmetry "cut losers short"): an UNPROVEN position (never
# armed breakeven) is flagged for market close after time_stop_bars; proven
# winners ride on; max_hold_bars is a hard cap for everyone.
# ---------------------------------------------------------------------------

def _ts_manager(trigger: float = 0.01, time_stop_bars: int = 0, max_hold_bars: int = 0) -> TrailingStopManager:
    return TrailingStopManager(
        atr_multiplier=1.5, atr_period=3,
        breakeven_trigger_pct=trigger,
        time_stop_bars=time_stop_bars, max_hold_bars=max_hold_bars,
    )


def _flat(mgr: TrailingStopManager, n: int, price: float = 50000.0) -> None:
    for _ in range(n):
        mgr.on_new_bar(_bar(high=price, low=price, close=price))


class TestTimeStop:
    def _register(self, mgr, side=Side.LONG, entry="50000", stop="49100"):
        mgr.register("p", side, "sl", Decimal(stop), Decimal(entry), Decimal("100"))

    def test_unproven_position_cut_at_time_stop_bars(self):
        mgr = _ts_manager(time_stop_bars=3)
        self._register(mgr)
        _flat(mgr, 2)                       # bars_held = 2 < 3 → nothing yet
        assert mgr.due_time_exits() == []
        _flat(mgr, 1)                       # bars_held = 3 → due
        exits = mgr.due_time_exits()
        assert len(exits) == 1
        assert exits[0].position_id == "p"
        assert exits[0].reason == "time_stop"
        assert exits[0].bars_held == 3
        assert exits[0].side == Side.LONG

    def test_proven_winner_is_exempt_from_time_stop(self):
        mgr = _ts_manager(time_stop_bars=3)
        self._register(mgr)
        mgr.on_new_bar(_bar(high=50500, low=50000, close=50100))  # +1% → arms breakeven
        _flat(mgr, 5)                       # well past time_stop_bars
        assert mgr.due_time_exits() == []   # be_armed → never time-stopped

    def test_max_hold_cap_closes_even_a_proven_winner(self):
        mgr = _ts_manager(time_stop_bars=0, max_hold_bars=3)
        self._register(mgr)
        mgr.on_new_bar(_bar(high=50500, low=50000, close=50100))  # armed (proven winner)
        _flat(mgr, 2)                       # bars_held = 3
        exits = mgr.due_time_exits()
        assert len(exits) == 1
        assert exits[0].reason == "max_hold"
        assert exits[0].bars_held == 3

    def test_disabled_never_flags(self):
        mgr = _ts_manager(time_stop_bars=0, max_hold_bars=0)
        self._register(mgr)
        _flat(mgr, 10)
        assert mgr.due_time_exits() == []

    def test_not_reported_after_on_close(self):
        mgr = _ts_manager(time_stop_bars=2)
        self._register(mgr)
        _flat(mgr, 2)
        assert len(mgr.due_time_exits()) == 1
        mgr.on_close("p")                   # caller closed it
        assert mgr.due_time_exits() == []   # gone → not re-reported

    def test_bars_held_counts_only_5m_bars(self):
        mgr = _ts_manager(time_stop_bars=1)
        self._register(mgr)
        mgr.on_new_bar(_bar(high=50000, low=50000, close=50000, interval=Interval.M15))
        assert mgr.due_time_exits() == []   # M15 must not age the position
        mgr.on_new_bar(_bar(high=50000, low=50000, close=50000))  # one real 5m bar
        assert len(mgr.due_time_exits()) == 1


# ---------------------------------------------------------------------------
# Persistence — trail state (peak / be_armed / bars_held / stop) survives a
# restart so winners keep running and the time-stop clock isn't reset.
# ---------------------------------------------------------------------------

def test_state_persists_and_reloads(tmp_path):
    f = tmp_path / "trail.json"
    mgr = TrailingStopManager(
        atr_multiplier=1.5, atr_period=3, breakeven_trigger_pct=0.01,
        time_stop_bars=48, state_file=f,
    )
    mgr.register("p", Side.LONG, "sl", Decimal("49100"), Decimal("50000"), Decimal("600"))
    mgr.on_new_bar(_bar(high=50500, low=50500, close=50500))  # arms BE, bars_held=1, stop→50000

    # a fresh manager on the same file recovers the full state (simulates restart)
    mgr2 = TrailingStopManager(
        atr_multiplier=1.5, atr_period=3, breakeven_trigger_pct=0.01,
        time_stop_bars=48, state_file=f,
    )
    assert mgr2.active_position_ids() == ["p"]
    st = mgr2._positions["p"]
    assert st.be_armed is True               # proven winner stays exempt from time-stop
    assert st.bars_held == 1                  # time-stop clock not reset
    assert st.entry_price == Decimal("50000")
    assert mgr2.get_current_stop("p") == Decimal("50000.00")  # breakeven floor preserved


def test_state_removed_on_close(tmp_path):
    f = tmp_path / "trail.json"
    mgr = TrailingStopManager(atr_multiplier=1.5, state_file=f)
    mgr.register("p", Side.LONG, "sl", Decimal("49100"), Decimal("50000"), Decimal("600"))
    mgr.on_close("p")
    mgr2 = TrailingStopManager(atr_multiplier=1.5, state_file=f)
    assert mgr2.active_position_ids() == []   # closed position not resurrected


def test_no_persistence_without_state_file(tmp_path):
    # default (no state_file) must not touch disk — keeps the unit tests pure
    mgr = TrailingStopManager(atr_multiplier=1.5)
    mgr.register("p", Side.LONG, "sl", Decimal("49100"), Decimal("50000"), Decimal("600"))
    assert not (tmp_path / "trail.json").exists()
