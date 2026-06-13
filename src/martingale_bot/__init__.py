"""martingale_bot — Bitget Spot Martingale (DCA) bot, replicated faithfully.

Self-contained package (like ``turtle_bot``), separate from ``src/bot/`` (the
Confluence momentum bot). Replicates the Bitget "Spot Martingale" strategy bot
(BGB/USDT · Normal · Aggressive preset) shown in the product screenshot:

    Price drop steps      1%
    Single-cycle TP       1%
    Max safety orders     5
    Starting condition    Immediate trigger
    Safety order params   2.50x (volume scale) | 1.00x (step scale)

All money values are ``decimal.Decimal`` (trading §1.2). Leverage is hard-capped
at 2x (§1.1); spot martingale runs 1x. Backtest-first, paper-gated (§1.3) — there
is NO live exchange integration here on purpose.

⚠️  Martingale is the inverse of this repo's hard-won winner-asymmetry know-how
(trading §8 R5 — "alpha ~90% is in the exit; cut losers short, let winners run").
It averages DOWN into losers and caps winners at +1%. See README.md "노하우 충돌".
"""

from .config import (
    CONFIG_AGGRESSIVE,
    MAX_LEVERAGE,
    PARAMS_AGGRESSIVE,
    BacktestConfig,
    MartingaleParams,
    max_cycle_cost,
)
from .grid import Grid, Leg, average_entry, build_grid, tp_price

__all__ = [
    "MartingaleParams",
    "BacktestConfig",
    "PARAMS_AGGRESSIVE",
    "CONFIG_AGGRESSIVE",
    "MAX_LEVERAGE",
    "max_cycle_cost",
    "Grid",
    "Leg",
    "build_grid",
    "average_entry",
    "tp_price",
]
