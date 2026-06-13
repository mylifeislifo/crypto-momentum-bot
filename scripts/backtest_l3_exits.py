"""Tune the live L3 exit discipline on REAL 5m price history.

Samples entries across a 5m series and runs the REAL exit logic
(`bot.research.l3_exit_sim.backtest_exits`) forward on each, then reports
exit-discipline stats — and can sweep `time_stop_bars` x `trail_atr_multiplier`
so the winner-asymmetry parameters can be tuned on real price action instead of
waiting for the sparse live confluence gate to fire.

    # no data — synthetic random walk, proves the pipeline end to end
    python scripts/backtest_l3_exits.py --demo --sweep

    # real Binance 5m klines (S3 dump or REST export); CSV with open/high/low/close
    python scripts/backtest_l3_exits.py --csv BTCUSDT-5m-2024.csv
    python scripts/backtest_l3_exits.py --csv BTCUSDT-5m-2024.csv --sweep

CSV: header with open/high/low/close columns (case-insensitive), OR a raw Binance
kline dump (no header → columns open_time,open,high,low,close,... are positional).

NOTE: nothing here trades. Exit-parameter changes still go through the trading
§1.3 gate (backtest -> walkforward -> paper 7d -> 10% seed) before any live use.
"""

import argparse
import csv as _csv
import logging
import random
import sys
from pathlib import Path

import structlog

structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from bot.core.enums import Side  # noqa: E402
from bot.research.l3_exit_sim import backtest_exits, bar  # noqa: E402

_OHLC = ("open", "high", "low", "close")


def load_csv(path: Path) -> list:
    """Load 5m bars from a CSV. Accepts a header with open/high/low/close columns
    (case-insensitive, extra columns ignored), or a header-less Binance kline dump
    (positional: open_time, open, high, low, close, ...)."""
    with open(path, newline="") as f:
        rows = list(_csv.reader(f))
    if not rows:
        raise ValueError(f"{path.name}: empty")
    header = [c.strip().lower() for c in rows[0]]
    if set(_OHLC) <= set(header):
        idx = {k: header.index(k) for k in _OHLC}
        data = rows[1:]
    else:                                  # raw Binance kline: open_time,open,high,low,close,...
        idx = {"open": 1, "high": 2, "low": 3, "close": 4}
        data = rows
    bars = []
    for i, r in enumerate(data):
        try:
            bars.append(bar(float(r[idx["high"]]), float(r[idx["low"]]), float(r[idx["close"]]), i=i))
        except (ValueError, IndexError):
            continue                       # skip malformed / non-numeric rows
    if not bars:
        raise ValueError(f"{path.name}: no usable OHLC rows")
    return bars


def gen_demo(n: int = 20000, seed: int = 42) -> list:
    """Deterministic random walk with mild upward drift + intrabar wicks. Enough
    bars (~70 days of 5m) to produce thousands of sampled entries."""
    rng = random.Random(seed)
    price = 50000.0
    bars = []
    for i in range(n):
        ret = rng.gauss(0.00002, 0.0015)   # tiny drift, ~0.15%/bar vol
        price *= (1 + ret)
        hi = price * (1 + abs(rng.gauss(0, 0.0008)))
        lo = price * (1 - abs(rng.gauss(0, 0.0008)))
        bars.append(bar(hi, lo, price, i=i))
    return bars


def _run(bars, side, **cfg):
    return backtest_exits(bars, side=side, **cfg)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--demo", action="store_true", help="synthetic random walk (no data needed)")
    src.add_argument("--csv", type=Path, help="5m OHLC CSV (header'd or raw Binance kline)")
    ap.add_argument("--side", default="long", choices=["long", "short"])
    ap.add_argument("--entry-stride", type=int, default=24, help="enter every N bars (24=2h)")
    ap.add_argument("--horizon", type=int, default=1000, help="max bars simulated per trade")
    ap.add_argument("--time-stop-bars", type=int, default=48)
    ap.add_argument("--trail-mult", type=float, default=1.5)
    ap.add_argument("--sweep", action="store_true", help="grid time_stop_bars x trail_mult")
    args = ap.parse_args()

    bars = gen_demo() if args.demo else load_csv(args.csv)
    side = Side.LONG if args.side == "long" else Side.SHORT

    print("=" * 78)
    print(f"L3 exit-discipline backtest   bars={len(bars)}  side={side.value}  "
          f"entry_stride={args.entry_stride}")
    print("=" * 78)

    if not args.sweep:
        stats = _run(bars, side, entry_stride=args.entry_stride, horizon=args.horizon,
                     time_stop_bars=args.time_stop_bars, atr_multiplier=args.trail_mult)
        print(f"[time_stop={args.time_stop_bars}  trail_mult={args.trail_mult}]")
        print("  " + stats.report().replace("\n", "\n  "))
        print("=" * 78)
        return 0

    # parameter sweep — compare exit configs on the identical entry set
    print(f"  {'time_stop':>9} {'trail_x':>8} {'n':>6} {'win':>6} {'E[r]':>8} "
          f"{'asym':>6} {'whip':>6}  proven")
    print("-" * 78)
    for ts in (24, 48, 96, 0):              # 0 = time stop disabled
        for tm in (1.5, 2.0, 3.0):
            s = _run(bars, side, entry_stride=args.entry_stride, horizon=args.horizon,
                     time_stop_bars=ts, atr_multiplier=tm)
            asym = "inf" if s.asymmetry_ratio == float("inf") else f"{s.asymmetry_ratio:.2f}"
            print(f"  {ts:>9} {tm:>8.1f} {s.n_trades:>6} {s.win_rate:>6.1%} "
                  f"{s.mean_return:>+8.3%} {asym:>6} {s.whipsaw_frac:>6.1%}  {s.proven_frac:.0%}")
    print("-" * 78)
    print("  read: want high asymmetry (winners held >> losers), +E[r], low whipsaw")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
