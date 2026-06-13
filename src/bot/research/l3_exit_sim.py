"""Deterministic simulation of the LIVE L3 exit discipline over a price path.

Drives the REAL production exit code — `risk.trail.TrailingStopManager` (ATR
chandelier trail + breakeven floor + conditional time-stop) — bar by bar over a
synthetic 5m price path, simulating how the resting stop fills and how the time
stop force-closes. No copy of the logic, no market data, no network: this is the
`trading §1.3` backtest gate step for the exit *discipline* (not an alpha signal),
and it exists to verify that the pieces from #13/#14/#15 actually compose into the
intended **winner-asymmetry** ("let winners run, cut losers short") rather than
just passing unit tests in isolation.

Fill model (LONG; SHORT mirrors):
  - the resting stop in effect during bar T is the `current_stop` set at the close
    of bar T-1 (or the initial stop on the entry bar). If bar T's low pierces it,
    the server-side STOP_MARKET fills at that stop price (intrabar) — checked
    BEFORE the trail moves on this bar's close (no look-ahead).
  - otherwise the bar closes, the trail advances (`on_new_bar`), and the time stop
    is consulted (`due_time_exits`); a time stop fills at the bar close (market).

Numbers use Decimal end-to-end (trading §1.2). This module depends only on the bot
package + stdlib.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from statistics import fmean, median

from ..core.enums import Interval, Side
from ..core.types import Bar
from ..risk.trail import TrailingStopManager, compute_atr

_T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
_STEP = timedelta(minutes=5)


@dataclass(frozen=True)
class ExitOutcome:
    reason: str          # stop_loss | breakeven | trail_profit | time_stop | max_hold | open
    exit_price: Decimal
    bars_held: int
    net_return: Decimal  # (exit-entry)/entry for LONG; mirror for SHORT
    be_armed: bool       # was the position ever a "proven" winner (breakeven armed)?

    def __str__(self) -> str:
        return (f"{self.reason:<12} held={self.bars_held:>4}  "
                f"net={self.net_return:+.2%}  be_armed={self.be_armed}")


def bar(high: float, low: float, close: float, *, i: int = 0,
        interval: Interval = Interval.M5) -> Bar:
    """One 5m bar from explicit high/low/close (open defaults to close)."""
    return Bar(
        ts=_T0 + i * _STEP, interval=interval,
        open=Decimal(str(close)), high=Decimal(str(high)),
        low=Decimal(str(low)), close=Decimal(str(close)),
        volume=Decimal("10"), buy_volume=Decimal("6"), sell_volume=Decimal("4"),
        cvd_delta=0.0, cvd_cumulative=0.0, vwap=Decimal(str(close)), trade_count=100,
    )


def path_bars(highs: list[float], lows: list[float], closes: list[float]) -> list[Bar]:
    if not (len(highs) == len(lows) == len(closes)):
        raise ValueError("highs/lows/closes must be equal length")
    return [bar(h, lo, c, i=i) for i, (h, lo, c) in enumerate(zip(highs, lows, closes))]


def _classify_price_stop(side: Side, stop: Decimal, entry: Decimal) -> str:
    """A resting-stop fill is a loss only if the stop sits the wrong side of entry;
    at/through breakeven it is a protected exit (winner-asymmetry made visible)."""
    if side == Side.LONG:
        if stop > entry:
            return "trail_profit"
        return "breakeven" if stop == entry else "stop_loss"
    if stop < entry:
        return "trail_profit"
    return "breakeven" if stop == entry else "stop_loss"


def simulate(
    bars: list[Bar],
    *,
    side: Side,
    entry_price: Decimal,
    initial_stop: Decimal,
    atr0: Decimal,
    breakeven_trigger_pct: float = 0.01,
    breakeven_offset_pct: float = 0.0,
    time_stop_bars: int = 48,
    max_hold_bars: int = 0,
    atr_multiplier: float = 1.5,
    atr_period: int = 5,
) -> ExitOutcome:
    """Walk the path through the real TrailingStopManager and return the realised exit."""
    mgr = TrailingStopManager(
        atr_multiplier=atr_multiplier, atr_period=atr_period,
        breakeven_trigger_pct=breakeven_trigger_pct, breakeven_offset_pct=breakeven_offset_pct,
        time_stop_bars=time_stop_bars, max_hold_bars=max_hold_bars,
    )
    mgr.register("p", side, "sl", initial_stop, entry_price, atr0)

    def ret(exit_price: Decimal) -> Decimal:
        if side == Side.LONG:
            return (exit_price - entry_price) / entry_price
        return (entry_price - exit_price) / entry_price

    held = 0
    last_close = entry_price
    for b in bars:
        last_close = b.close
        stop = mgr.get_current_stop("p")
        # 1) resting stop fill (intrabar) using the stop in effect from the prior close
        if stop is not None:
            hit = (side == Side.LONG and b.low <= stop) or (side == Side.SHORT and b.high >= stop)
            if hit:
                be = mgr._positions["p"].be_armed
                return ExitOutcome(_classify_price_stop(side, stop, entry_price), stop, held, ret(stop), be)
        # 2) bar closes → trail advances + time stop is consulted
        mgr.on_new_bar(b)
        held += 1
        exits = mgr.due_time_exits()
        if exits:
            be = mgr._positions["p"].be_armed
            return ExitOutcome(exits[0].reason, b.close, held, ret(b.close), be)

    be = mgr._positions["p"].be_armed
    return ExitOutcome("open", last_close, held, ret(last_close), be)


# ============================================================================
# Multi-entry backtest — tune the exit discipline on REAL price paths
# ============================================================================
#
# To tune exit parameters (time_stop_bars, trail multiplier, breakeven) we need
# many realised exits, not the handful the strict live confluence gate produces.
# The exit discipline is entry-agnostic (R5: "alpha is in the exit"), so we
# sample entries at a fixed stride across a real 5m series and measure the exit
# OUTCOME distribution under a given config. Comparing configs over the SAME
# entry set isolates the effect of the exit rule.


@dataclass
class ExitStats:
    n_trades: int
    win_rate: float
    mean_return: float          # expectancy per trade (fraction)
    median_return: float
    proven_frac: float          # fraction that armed breakeven (became "winners")
    avg_hold_winners: float     # mean bars_held for net>0 trades
    avg_hold_losers: float      # mean bars_held for net<=0 trades
    asymmetry_ratio: float      # avg_hold_winners / avg_hold_losers (1491 target ~3.8)
    whipsaw_frac: float         # unproven trades stopped early at a loss (pre-proof trail cut)
    reason_counts: dict[str, int]

    def report(self) -> str:
        rc = "  ".join(f"{k}={v}" for k, v in sorted(self.reason_counts.items()))
        return (
            f"n={self.n_trades}  win={self.win_rate:.1%}  E[r]={self.mean_return:+.3%}  "
            f"med={self.median_return:+.3%}  proven={self.proven_frac:.1%}\n"
            f"    hold winners={self.avg_hold_winners:.0f}  losers={self.avg_hold_losers:.0f}  "
            f"asymmetry={self.asymmetry_ratio:.2f}x  whipsaw={self.whipsaw_frac:.1%}\n"
            f"    exits: {rc}"
        )


# A whipsaw = an unproven position stopped at a loss within this many bars (~30min),
# i.e. the pre-proof trail clipped it before it had a chance to run. FIXED (not derived
# from time_stop_bars) so the metric is comparable across a parameter sweep.
_WHIPSAW_WINDOW_BARS = 6


def _aggregate(outcomes: list[ExitOutcome]) -> ExitStats:
    n = len(outcomes)
    if n == 0:
        return ExitStats(0, float("nan"), float("nan"), float("nan"), float("nan"),
                         float("nan"), float("nan"), float("nan"), float("nan"), {})
    rets = [float(o.net_return) for o in outcomes]
    hold_win = [o.bars_held for o in outcomes if float(o.net_return) > 0]
    hold_loss = [o.bars_held for o in outcomes if float(o.net_return) <= 0]
    awh = fmean(hold_win) if hold_win else 0.0
    awl = fmean(hold_loss) if hold_loss else 0.0
    whipsaw = sum(
        1 for o in outcomes
        if (not o.be_armed) and o.reason == "stop_loss"
        and float(o.net_return) < 0 and o.bars_held <= _WHIPSAW_WINDOW_BARS
    )
    return ExitStats(
        n_trades=n,
        win_rate=sum(1 for r in rets if r > 0) / n,
        mean_return=fmean(rets),
        median_return=float(median(rets)),
        proven_frac=sum(1 for o in outcomes if o.be_armed) / n,
        avg_hold_winners=awh,
        avg_hold_losers=awl,
        asymmetry_ratio=(awh / awl if awl else float("inf")),
        whipsaw_frac=whipsaw / n,
        reason_counts=dict(Counter(o.reason for o in outcomes)),
    )


def backtest_exits(
    bars: list[Bar],
    *,
    side: Side = Side.LONG,
    entry_stride: int = 24,
    horizon: int = 1000,
    warmup: int = 50,
    long_sl_pct: float = -0.018,
    short_sl_pct: float = -0.0075,
    breakeven_trigger_pct: float = 0.01,
    breakeven_offset_pct: float = 0.0012,
    time_stop_bars: int = 48,
    max_hold_bars: int = 0,
    atr_multiplier: float = 1.5,
    atr_period: int = 5,
) -> ExitStats:
    """Sample entries every `entry_stride` bars (after `warmup`) and run the real
    L3 exit logic forward up to `horizon` bars each. Returns aggregate exit-discipline
    stats for the given config — the per-config row of a tuning sweep."""
    outcomes: list[ExitOutcome] = []
    n = len(bars)
    for i in range(warmup, n - 1, entry_stride):
        entry_price = bars[i].open
        if side == Side.LONG:
            initial_stop = (entry_price * (Decimal("1") + Decimal(str(long_sl_pct)))).quantize(Decimal("0.01"))
        else:
            initial_stop = (entry_price * (Decimal("1") + Decimal(str(abs(short_sl_pct))))).quantize(Decimal("0.01"))
        atr0 = compute_atr(bars[max(0, i - warmup):i], atr_period)
        if atr0 <= 0:
            atr0 = entry_price * Decimal("0.005")
        outcomes.append(simulate(
            bars[i:i + horizon], side=side, entry_price=entry_price,
            initial_stop=initial_stop, atr0=atr0,
            breakeven_trigger_pct=breakeven_trigger_pct, breakeven_offset_pct=breakeven_offset_pct,
            time_stop_bars=time_stop_bars, max_hold_bars=max_hold_bars,
            atr_multiplier=atr_multiplier, atr_period=atr_period,
        ))
    return _aggregate(outcomes)
