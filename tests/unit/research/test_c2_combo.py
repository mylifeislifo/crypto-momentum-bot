"""Unit tests for the C-2 × 1491-exit × regime backtest harness.

These run with numpy only (no market data, no network). They prove the
look-ahead-safety and exit-truncation claims that the whole hypothesis rests
on — the parts that, if wrong, would silently fabricate alpha
(signal-validation §2.4).
"""

from decimal import Decimal

import numpy as np
import pytest

from bot.research import c2_combo as cc
from bot.research.c2_combo import (
    CostParams,
    EntryParams,
    ExitMode,
    ExitParams,
    MarketArrays,
    RegimeParams,
)

_STEP = 300  # 5 minutes in seconds


def make_market(*, opens, highs, lows, closes, oi, buy, sell, t0=1_700_000_000):
    n = len(opens)
    ts = np.array([t0 + i * _STEP for i in range(n)], dtype=float)

    def arr(x):
        return np.asarray(x, dtype=float)

    return MarketArrays(
        ts=ts, open=arr(opens), high=arr(highs), low=arr(lows), close=arr(closes),
        oi=arr(oi), taker_buy=arr(buy), taker_sell=arr(sell),
    )


# --------------------------------------------------------------------------
# derived signals
# --------------------------------------------------------------------------

def test_oi_delta_first_bar_is_nan_and_value_correct():
    oi = np.array([100.0, 99.0, 90.0])
    d = cc.oi_delta_pct(oi)
    assert np.isnan(d[0])
    assert d[1] == pytest.approx(-0.01)
    assert d[2] == pytest.approx((90 - 99) / 99)


def test_taker_ratio_handles_zero_sell():
    r = cc.taker_ratio(np.array([5.0, 9.0]), np.array([10.0, 0.0]))
    assert r[0] == pytest.approx(0.5)
    assert np.isnan(r[1])


def test_capitulation_requires_both_conditions():
    p = EntryParams()  # oi<=-0.005, taker<0.90
    oi_d = np.array([-0.01, -0.01, -0.001, np.nan])
    tr = np.array([0.80, 0.95, 0.50, 0.50])
    flag = cc.capitulation_flag(oi_d, tr, p)
    assert list(flag) == [True, False, False, False]


# --------------------------------------------------------------------------
# trailing quantile — the look-ahead-safety guard (§2.4)
# --------------------------------------------------------------------------

def test_trailing_quantile_excludes_current_bar_and_warms_up():
    x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    out = cc.trailing_quantile(x, q=0.5, window=100, warmup=2)
    # t=0,1 → fewer than warmup past obs → nan
    assert np.isnan(out[0]) and np.isnan(out[1])
    # t=2 → median of past {1,2} = 1.5  (current bar 3 excluded)
    assert out[2] == pytest.approx(1.5)
    # t=4 → median of past {1,2,3,4} = 2.5  (current bar 5 excluded)
    assert out[4] == pytest.approx(2.5)


def test_trailing_quantile_window_is_bounded():
    x = np.arange(10, dtype=float)
    out = cc.trailing_quantile(x, q=0.0, window=3, warmup=1)
    # t=5 → min of past window [t-3, t-1] = {2,3,4} = 2
    assert out[5] == pytest.approx(2.0)


# --------------------------------------------------------------------------
# exit simulation — the core "alpha is in the exit" mechanism (R5)
# --------------------------------------------------------------------------

def test_trailing_stop_truncates_catastrophic_loss_to_2pct():
    """C-2's killers were -8%/-10% under a no-stop 24h hold. The 1491 exit must
    cut them at -2%. Entry at open of bar 1 = 100; bar 1 craters to low 85."""
    m = make_market(
        opens=[100, 100, 100], highs=[100, 100, 100],
        lows=[100, 85, 80], closes=[100, 86, 81],
        oi=[100, 100, 100], buy=[1, 1, 1], sell=[1, 1, 1],
    )
    cost = CostParams()
    t = cc._simulate_one(m, entry_idx=1, exit_p=ExitParams(), cost=cost, mode=ExitMode.TRAILING_1491)
    assert t.exit_reason == "stop_loss"
    # exit at stop = 98 → -2% minus round-trip cost
    assert float(t.return_pct) == pytest.approx(-0.02 - cost.round_trip, abs=1e-9)


def test_fixed_hold_does_not_stop_reproducing_c2_failure():
    """Same crash, but the baseline fixed-hold mode has no stop → full loss."""
    m = make_market(
        opens=[100, 100, 100, 100], highs=[100, 100, 100, 100],
        lows=[100, 85, 80, 80], closes=[100, 86, 81, 90],
        oi=[100, 100, 100, 100], buy=[1, 1, 1, 1], sell=[1, 1, 1, 1],
    )
    cost = CostParams()
    ep = ExitParams(fixed_hold_bars=2)
    t = cc._simulate_one(m, entry_idx=1, exit_p=ep, cost=cost, mode=ExitMode.FIXED_HOLD)
    assert t.exit_reason == "fixed_hold"
    # held to close of bar 3 (=1+2) = 90 → -10% minus cost
    assert float(t.return_pct) == pytest.approx(-0.10 - cost.round_trip, abs=1e-9)


def test_breakeven_exits_near_zero_not_at_stop():
    m = make_market(
        opens=[100, 100, 100], highs=[100, 102, 100.5], lows=[100, 100.5, 99],
        closes=[100, 101, 99.5], oi=[100, 100, 100], buy=[1, 1, 1], sell=[1, 1, 1],
    )
    cost = CostParams()
    t = cc._simulate_one(m, entry_idx=1, exit_p=ExitParams(), cost=cost, mode=ExitMode.TRAILING_1491)
    assert t.exit_reason == "breakeven"
    # breakeven stop = entry (100) → 0% gross minus cost
    assert float(t.return_pct) == pytest.approx(-cost.round_trip, abs=1e-9)


def test_gap_through_stop_fills_at_open_worse_than_stop():
    m = make_market(
        opens=[100, 100, 95], highs=[100, 100, 96], lows=[100, 99, 94],
        closes=[100, 99.5, 95], oi=[100, 100, 100], buy=[1, 1, 1], sell=[1, 1, 1],
    )
    cost = CostParams()
    t = cc._simulate_one(m, entry_idx=1, exit_p=ExitParams(), cost=cost, mode=ExitMode.TRAILING_1491)
    assert t.exit_reason == "gap_stop"
    # bar 2 opened at 95 (below stop 98) → fills at 95 → -5% minus cost
    assert float(t.return_pct) == pytest.approx(-0.05 - cost.round_trip, abs=1e-9)


def test_max_hold_exit_when_never_stopped():
    m = make_market(
        opens=[100, 100, 101, 102, 103], highs=[100, 100.5, 101.5, 102.5, 103.5],
        lows=[100, 99.5, 100.5, 101.5, 102.5], closes=[100, 100, 101, 102, 103],
        oi=[100] * 5, buy=[1] * 5, sell=[1] * 5,
    )
    t = cc._simulate_one(
        m, entry_idx=1, exit_p=ExitParams(max_hold_bars=3, breakeven_trigger_pct=99.0),
        cost=CostParams(), mode=ExitMode.TRAILING_1491,
    )
    assert t.exit_reason == "max_hold"
    assert t.bars_held == 3  # exited at close of bar 4 (=1+3)


# --------------------------------------------------------------------------
# entry timing — look-ahead safety (§2.4): enter at NEXT bar open
# --------------------------------------------------------------------------

def test_entry_executes_at_next_bar_open():
    # capitulation at bar 1 (oi drops 1%, taker 0.5); entry must be bar 2's open.
    m = make_market(
        opens=[100, 100, 50, 50, 50], highs=[100, 100, 50, 50, 50],
        lows=[100, 100, 50, 50, 50], closes=[100, 100, 50, 50, 50],
        oi=[100, 99, 99, 99, 99], buy=[1, 1, 1, 1, 1], sell=[1, 2, 1, 1, 1],
    )
    trades = cc.scan_and_simulate(
        m, ExitMode.FIXED_HOLD, regime_p=RegimeParams(require="off"),
        exit_p=ExitParams(fixed_hold_bars=1),
    )
    assert len(trades) == 1
    assert trades[0].entry_ts == int(m.ts[2])          # bar after the signal bar
    assert trades[0].entry_price == Decimal("50")       # open of bar 2, not bar 1


def test_no_overlapping_positions():
    # two consecutive capitulation bars; only one position can be open at a time
    m = make_market(
        opens=[100] * 8, highs=[100] * 8, lows=[100] * 8, closes=[100] * 8,
        oi=[100, 99, 98, 97, 96, 95, 94, 93],
        buy=[1] * 8, sell=[1, 2, 2, 2, 2, 2, 2, 2],
    )
    trades = cc.scan_and_simulate(
        m, ExitMode.FIXED_HOLD, regime_p=RegimeParams(require="off"),
        exit_p=ExitParams(fixed_hold_bars=3),
    )
    # entries cannot overlap: each trade holds 3 bars, so they must be spaced out
    for a, b in zip(trades, trades[1:]):
        assert b.entry_ts >= a.exit_ts


# --------------------------------------------------------------------------
# regime gate
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "require,n1,n2,expected",
    [
        ("off", False, False, True),
        ("n1", True, False, True),
        ("n1", False, True, False),
        ("n2", False, True, True),
        ("both", True, True, True),
        ("both", True, False, False),
        ("any", False, True, True),
        ("any", False, False, False),
    ],
)
def test_regime_gate_logic(require, n1, n2, expected):
    assert cc._regime_ok(RegimeParams(require=require), n1, n2) is expected


def test_n2_systemic_gate_blocks_isolated_capitulation():
    base = dict(
        opens=[100] * 4, highs=[100] * 4, lows=[100] * 4, closes=[100] * 4,
        oi=[100, 99, 99, 99], buy=[1, 1, 1, 1], sell=[1, 2, 1, 1],
    )
    m = make_market(**base)
    rp = RegimeParams(require="n2", min_systemic_symbols=3)
    # only 1 symbol capitulating → blocked
    blocked = cc.scan_and_simulate(
        m, ExitMode.FIXED_HOLD, regime_p=rp, exit_p=ExitParams(fixed_hold_bars=1),
        systemic_count=np.array([0, 1, 0, 0]),
    )
    assert blocked == []
    # 3 symbols capitulating at the signal bar → allowed
    allowed = cc.scan_and_simulate(
        m, ExitMode.FIXED_HOLD, regime_p=rp, exit_p=ExitParams(fixed_hold_bars=1),
        systemic_count=np.array([0, 3, 0, 0]),
    )
    assert len(allowed) == 1


def test_systemic_capitulation_count_aligns_by_timestamp():
    target_ts = np.array([10, 20, 30], dtype=float)
    flags = {
        "A": np.array([True, False, True]),
        "B": np.array([True, True, False]),
    }
    ts = {"A": np.array([10, 20, 30.0]), "B": np.array([10, 20, 30.0])}
    count = cc.systemic_capitulation_count(flags, ts, target_ts)
    assert list(count) == [2, 1, 1]


# --------------------------------------------------------------------------
# statistics (§2.2 / §2.3)
# --------------------------------------------------------------------------

def test_block_bootstrap_is_reproducible():
    r = np.array([0.01, -0.02, 0.03, -0.01, 0.02, 0.04, -0.03, 0.01])
    a = cc.block_bootstrap_ci(r, seed=42)
    b = cc.block_bootstrap_ci(r, seed=42)
    assert a == b


def test_block_bootstrap_constant_positive_series_is_significant():
    r = np.full(50, 0.05)
    point, lo, hi = cc.block_bootstrap_ci(r)
    assert point == pytest.approx(0.05)
    assert lo > 0.0  # CI strictly above zero


def test_block_bootstrap_zero_mean_series_includes_zero():
    r = np.array([0.05, -0.05] * 25)
    _, lo, hi = cc.block_bootstrap_ci(r)
    assert lo < 0.0 < hi  # not significant


def test_independent_episodes_clusters_within_gap():
    # three trades within an hour, then one a month later → 2 episodes
    t0 = 1_700_000_000
    entry = np.array([t0, t0 + 600, t0 + 1200, t0 + 30 * 86400], dtype=float)
    assert cc.count_independent_episodes(entry, gap_days=7.0) == 2


def test_independent_episodes_empty():
    assert cc.count_independent_episodes(np.array([])) == 0


# --------------------------------------------------------------------------
# exit-alpha isolation (R5) — the headline comparison
# --------------------------------------------------------------------------

def test_exit_alpha_trailing_beats_fixed_on_a_crash_entry():
    """Identical C-2 entry, two exits. The crash makes fixed-hold lose big while
    the 1491 stop truncates the loss → trailing mean must exceed fixed mean.
    This is the mechanical core of 'alpha ~90% is in the exit' (R5)."""
    m = make_market(
        opens=[100, 100, 100, 100, 100], highs=[100, 100, 100, 100, 100],
        lows=[100, 100, 80, 75, 70], closes=[100, 100, 82, 76, 70],
        oi=[100, 99, 99, 99, 99], buy=[1, 1, 1, 1, 1], sell=[1, 2, 1, 1, 1],
    )
    res = cc.run_comparison(
        m, regime_p=RegimeParams(require="off"),
        exit_p=ExitParams(fixed_hold_bars=3, max_hold_bars=3),
    )
    fixed = res[ExitMode.FIXED_HOLD.value]
    trail = res[ExitMode.TRAILING_1491.value]
    assert fixed.n_trades == 1 and trail.n_trades == 1
    assert float(trail.mean_return) > float(fixed.mean_return)
    # the trailing exit caps the loss near -2%, fixed eats the full drawdown
    assert float(trail.worst_trade) > float(fixed.worst_trade)
    assert float(trail.worst_trade) == pytest.approx(-0.02 - CostParams().round_trip, abs=1e-9)
