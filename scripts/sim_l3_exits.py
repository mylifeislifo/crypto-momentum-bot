"""Verify the live L3 exit discipline produces winner-asymmetry on canonical paths.

Runs `bot.research.l3_exit_sim` (which drives the REAL TrailingStopManager) over a
handful of deterministic 5m price scenarios and prints the realised exit of each.
The point is to SEE — not just unit-assert — that proven winners run while
unproven/losing positions are cut short. No network, no market data.

    python scripts/sim_l3_exits.py
"""

import logging
import sys
from decimal import Decimal
from pathlib import Path

import structlog

structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from bot.core.enums import Side  # noqa: E402
from bot.research.l3_exit_sim import ExitOutcome, path_bars, simulate  # noqa: E402

ENTRY = Decimal("50000")
LONG_STOP = Decimal("49100")     # -1.8% initial (config long_sl_pct)
ATR0 = Decimal("250")            # production fallback seed (entry * 0.5%)
_CFG = dict(breakeven_trigger_pct=0.01, breakeven_offset_pct=0.0012,
            time_stop_bars=48, max_hold_bars=0)


def _long(highs, lows, closes) -> ExitOutcome:
    return simulate(path_bars(highs, lows, closes), side=Side.LONG,
                    entry_price=ENTRY, initial_stop=LONG_STOP, atr0=ATR0, **_CFG)


def runaway_winner() -> ExitOutcome:
    # +0.2%/bar for 200 bars with tight wicks → never dips to the trail, runs all the way
    closes = [50000 * (1.002 ** i) for i in range(1, 201)]
    highs = [c * 1.001 for c in closes]
    lows = [c * 0.999 for c in closes]
    return _long(highs, lows, closes)


def breakeven_save() -> ExitOutcome:
    # spike +1.6% (arms breakeven, wide range → big ATR so the trail sits below entry),
    # then revert straight to entry → exits at the breakeven floor, NOT the -1.8% stop
    highs = [50800, 50200]
    lows = [50100, 49990]
    closes = [50700, 50000]
    return _long(highs, lows, closes)


def slow_grind_unproven() -> ExitOutcome:
    # drifts to +0.5% and sits there (never +1% → never "proven") for 60 bars →
    # time stop cuts it loose even though slightly green ("free the capital")
    highs = [50270] * 60
    lows = [50230] * 60
    closes = [50250] * 60
    return _long(highs, lows, closes)


def quick_crash() -> ExitOutcome:
    # one flat bar, then a crash straight through the stop
    highs = [50050, 49900]
    lows = [49850, 49000]
    closes = [49900, 49100]
    return _long(highs, lows, closes)


def whipsaw_chop() -> ExitOutcome:
    # chops below +1% (never proven) intending to break out later — but the tight
    # ATR×1.5 trail (which starts tightening from entry) whipsaws it out first.
    # Surfaces a real tuning question: the pre-proof trail can cut future winners.
    chop_c = [50100, 49950, 50150, 49980, 50120] * 4          # 20 bars, all < +1%
    run_c = [50000 * (1.004 ** i) for i in range(1, 41)]       # would-be breakout
    closes = chop_c + run_c
    highs = [c * 1.0015 for c in closes]
    lows = [c * 0.9985 for c in closes]
    return _long(highs, lows, closes)


SCENARIOS = {
    "runaway_winner": runaway_winner,
    "breakeven_save": breakeven_save,
    "slow_grind_unproven": slow_grind_unproven,
    "quick_crash": quick_crash,
    "whipsaw_chop": whipsaw_chop,
}


def main() -> int:
    print("=" * 76)
    print("LIVE L3 exit discipline — winner-asymmetry verification (synthetic paths)")
    print(f"entry={ENTRY}  initial_stop={LONG_STOP}  cfg={_CFG}")
    print("=" * 76)
    print(f"  {'scenario':<22}{'reason':<13}{'held':>6}{'net':>10}  proven")
    print("-" * 76)
    results = {}
    for name, fn in SCENARIOS.items():
        o = fn()
        results[name] = o
        print(f"  {name:<22}{o.reason:<13}{o.bars_held:>6}{float(o.net_return):>+9.2%}  {o.be_armed}")
    print("-" * 76)

    winner = results["runaway_winner"]
    cut = [results["slow_grind_unproven"], results["quick_crash"], results["whipsaw_chop"]]
    max_cut_held = max(c.bars_held for c in cut)
    print(f"  winner-asymmetry: winner held={winner.bars_held} (+{float(winner.net_return):.0%})"
          f"  >>  longest cut held={max_cut_held}")
    print(f"  protected: breakeven_save exited {float(results['breakeven_save'].net_return):+.2%} "
          f"(initial stop would have been -1.80%)")
    print("  finding: whipsaw_chop cut at "
          f"{results['whipsaw_chop'].bars_held} bars — the pre-proof ATR trail can clip a"
          " would-be winner (tuning candidate)")
    print("=" * 76)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
