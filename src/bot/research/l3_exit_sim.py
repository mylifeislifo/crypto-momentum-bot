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

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from ..core.enums import Interval, Side
from ..core.types import Bar
from ..risk.trail import TrailingStopManager

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
