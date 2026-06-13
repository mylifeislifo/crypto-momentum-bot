"""Behaviour verification of the LIVE L3 exit discipline (winner-asymmetry).

Drives the real TrailingStopManager (via l3_exit_sim) over canonical price paths
and asserts the *emergent* outcome — the thing unit tests of the parts cannot
show: proven winners run, unproven/losing positions are cut short, and a winner
that reverts never round-trips to the initial stop.
"""

import logging
import random
from decimal import Decimal

import structlog

from bot.core.enums import Side
from bot.research.l3_exit_sim import backtest_exits, path_bars, simulate

structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))

_ENTRY = Decimal("50000")
_LONG_STOP = Decimal("49100")   # -1.8%
_ATR0 = Decimal("250")


def _smooth(closes, wick=0.001):
    return [c * (1 + wick) for c in closes], [c * (1 - wick) for c in closes], closes


def _long(highs, lows, closes):
    return simulate(
        path_bars(highs, lows, closes), side=Side.LONG, entry_price=_ENTRY,
        initial_stop=_LONG_STOP, atr0=_ATR0, breakeven_trigger_pct=0.01,
        breakeven_offset_pct=0.0012, time_stop_bars=48, max_hold_bars=0,
    )


def test_runaway_winner_runs_full_path_and_is_proven():
    o = _long(*_smooth([float(_ENTRY) * (1.002 ** i) for i in range(1, 201)]))
    assert o.reason == "open"            # still running at the end (never stopped)
    assert o.bars_held == 200
    assert o.net_return > Decimal("0.4")  # ~+49%
    assert o.be_armed is True


def test_unproven_grind_is_time_stopped_at_the_window():
    # +0.5% and flat: never proven (<+1%), the trail never binds → time stop cuts it
    o = _long([50270] * 60, [50230] * 60, [50250] * 60)
    assert o.reason == "time_stop"
    assert o.bars_held == 48
    assert o.be_armed is False


def test_quick_crash_stops_out_fast_at_a_loss():
    o = _long([50050, 49900], [49850, 49000], [49900, 49100])
    assert o.reason == "stop_loss"
    assert o.net_return < 0
    assert o.bars_held <= 2
    assert o.be_armed is False


def test_reverting_winner_is_protected_not_round_tripped():
    # spikes +1.6% (proven) then reverts to entry → exits protected, never the -1.8% stop
    o = _long([50800, 50200], [50100, 49990], [50700, 50000])
    assert o.be_armed is True
    assert o.net_return >= 0          # winner cannot become a loss


def test_winner_held_far_longer_than_every_cut():
    winner = _long(*_smooth([float(_ENTRY) * (1.002 ** i) for i in range(1, 201)]))
    grind = _long([50270] * 60, [50230] * 60, [50250] * 60)
    crash = _long([50050, 49900], [49850, 49000], [49900, 49100])
    # asymmetry: the proven winner outlives and out-earns the cut cases
    assert winner.bars_held > grind.bars_held > crash.bars_held
    assert winner.net_return > grind.net_return
    assert winner.net_return > crash.net_return


def test_short_winner_mirrors_long():
    closes = [float(_ENTRY) * (0.998 ** i) for i in range(1, 201)]
    highs, lows, closes = _smooth(closes)
    o = simulate(
        path_bars(highs, lows, closes), side=Side.SHORT, entry_price=_ENTRY,
        initial_stop=Decimal("50375"), atr0=_ATR0, breakeven_trigger_pct=0.01,
        breakeven_offset_pct=0.0012, time_stop_bars=48, max_hold_bars=0,
    )
    assert o.net_return > Decimal("0.25")   # ~+33% (geometric; less than the long's +49%)
    assert o.be_armed is True


# ---------------------------------------------------------------------------
# Multi-entry backtest (real-data tuning harness) — verified on synthetic paths
# ---------------------------------------------------------------------------

def _smooth_bars(closes, wick=0.0005):
    return path_bars([c * (1 + wick) for c in closes],
                     [c * (1 - wick) for c in closes], closes)


def test_backtest_exits_uptrend_runs_winners_with_asymmetry():
    closes = [50000 * (1.001 ** i) for i in range(400)]   # steady uptrend
    s = backtest_exits(_smooth_bars(closes), side=Side.LONG, entry_stride=24, time_stop_bars=48)
    assert s.n_trades > 5
    assert s.proven_frac > 0.5                 # most entries reach +1% in an uptrend
    assert s.avg_hold_winners > s.avg_hold_losers   # winner-asymmetry
    assert s.mean_return > 0                   # uptrend → positive expectancy


def test_backtest_exits_reports_full_stats():
    closes = [50000 * (1.0005 ** i) for i in range(300)]
    s = backtest_exits(_smooth_bars(closes), side=Side.LONG, entry_stride=24)
    assert s.n_trades > 0
    assert 0.0 <= s.win_rate <= 1.0
    assert sum(s.reason_counts.values()) == s.n_trades
    assert "n=" in s.report()


def test_tighter_trail_whipsaws_at_least_as_much_as_loose():
    rng = random.Random(1)
    price, closes = 50000.0, []
    for _ in range(3000):                       # choppy flat random walk
        price *= (1 + rng.gauss(0, 0.001))
        closes.append(price)
    bars = path_bars([c * 1.001 for c in closes], [c * 0.999 for c in closes], closes)
    tight = backtest_exits(bars, atr_multiplier=1.5, time_stop_bars=48, entry_stride=24)
    loose = backtest_exits(bars, atr_multiplier=3.0, time_stop_bars=48, entry_stride=24)
    assert tight.n_trades == loose.n_trades     # identical entry set (config isolated)
    assert tight.whipsaw_frac >= loose.whipsaw_frac   # the #17 finding, quantified
