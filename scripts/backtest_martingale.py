"""M1 backtest of the Bitget Spot Martingale (DCA) bot on synthetic 5m paths.

Runs `martingale_bot.engine` over three deterministic scenarios — a grind-up, a
choppy range, and a sustained crash — to SEE the strategy's two faces: lots of
small +1% wins in normal tape, and the structural fat-tail bag when price falls
through the whole ladder. No network, no market data.

    python3 scripts/backtest_martingale.py

⚠️ Single-backtest positive ≠ alpha. Martingale almost always shows green here
because it wins often; the real risk is the rare stuck-bag loss (signal-validation
§2.1/§2.2). Decide on fat-tail size/frequency, not on average/APY.
"""

import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from martingale_bot.config import BacktestConfig, MartingaleParams, max_cycle_cost  # noqa: E402
from martingale_bot.engine import PriceBar, run_backtest  # noqa: E402

_T0 = datetime(2026, 6, 13, tzinfo=timezone.utc)


def _path(closes: list[float]) -> list[PriceBar]:
    """Wrap a close series in bars with ±0.1% wicks."""
    bars = []
    for i, c in enumerate(closes):
        cd = Decimal(str(c))
        bars.append(PriceBar(
            ts=_T0 + timedelta(minutes=5 * i),
            high=cd * Decimal("1.001"),
            low=cd * Decimal("0.999"),
            close=cd,
        ))
    return bars


def grind_up() -> list[PriceBar]:
    # gentle +0.15%/bar drift → repeated +1% take-profits
    return _path([100 * (1.0015 ** i) for i in range(120)])


def chop_range() -> list[PriceBar]:
    # oscillate ±1.5% around 100 → safety orders fill then recover
    import math
    return _path([100 * (1 + 0.015 * math.sin(i / 3)) for i in range(120)])


def crash() -> list[PriceBar]:
    # −0.6%/bar sustained downtrend → falls through the whole ladder → stuck bag
    return _path([100 * (0.994 ** i) for i in range(120)])


SCENARIOS = {"grind_up": grind_up, "chop_range": chop_range, "crash": crash}


def main() -> int:
    params = MartingaleParams()
    cfg = BacktestConfig()
    out_dir = cfg.results_dir

    print("=" * 84)
    print("Bitget Spot Martingale (DCA) — M1 backtest (synthetic 5m paths)")
    print(f"symbol={cfg.symbol}  params: drop={params.price_drop_step} tp={params.tp_target} "
          f"max_SO={params.max_safety_orders} vol_scale={params.volume_scale}")
    print(f"full-ladder capital requirement = {max_cycle_cost(params)} USDT "
          f"({max_cycle_cost(params) / params.base_order_size:.1f}x base order)")
    print("=" * 84)
    print(f"  {'scenario':<12}{'cycles':>7}{'TP':>5}{'maxSO':>7}{'pnl':>12}{'ret':>9}{'maxDD':>9}  stuck")
    print("-" * 84)
    for name, fn in SCENARIOS.items():
        res = run_backtest(fn(), params, cfg, log_path=out_dir / f"{name}_trades.jsonl")
        print(f"  {name:<12}{res.n_cycles:>7}{res.n_tp_cycles:>5}{res.max_safety_orders_used:>7}"
              f"{float(res.total_pnl):>+12.2f}{float(res.total_return_pct):>+8.2%}"
              f"{float(res.max_drawdown_pct):>+8.2%}  {res.ended_stuck}")
    print("-" * 84)
    print(f"  logs → {out_dir}/<scenario>_trades.jsonl  (re-parse with jq — bot-ops §2.2)")
    print("  note: 'crash' shows the fat-tail — stuck=True, large maxDD. That single regime")
    print("        decides viability, not the green 'grind_up'/'chop_range' rows.")
    print("=" * 84)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
