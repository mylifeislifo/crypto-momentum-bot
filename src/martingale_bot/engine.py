"""Single-bot martingale backtest engine (M1 — point estimate only).

Walks a bar series and runs DCA cycles: base order (immediate trigger) → safety
orders as price drops → take-profit sell of the whole position → next cycle.
Fully Decimal (trading §1.2). Emits a JSON Lines trade log (audit-log §2.1,
payload schema {symbol, side, qty, price, pnl} per trading §5) so results can be
re-parsed independently (bot-ops §2.2 신뢰성 0 — never trust the summary alone).

Intrabar convention (no look-ahead beyond the current bar): within one bar we
process the ADVERSE direction first — safety-order fills and the optional hard
stop are checked against ``bar.low``, THEN take-profit against ``bar.high``. This
is the conservative ordering for a bag-holding strategy (assume the dip happened
before the bounce). A cycle never opens and closes on the same bar.

⚠️  This is a backtest skeleton, not a live bot. Live trading requires the §1.3
gate (backtest → walkforward → paper 7d → 10% seed) and explicit user approval.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional

from .config import BacktestConfig, MartingaleParams
from .grid import Grid, build_grid, tp_price

_SOURCE = "martingale_bot"


@dataclass(frozen=True)
class PriceBar:
    ts: datetime
    high: Decimal
    low: Decimal
    close: Decimal


@dataclass(frozen=True)
class CycleResult:
    cycle_id: int
    started_ts: datetime
    ended_ts: datetime
    base_price: Decimal
    legs_filled: int            # base + safety orders that actually filled
    avg_entry: Decimal
    exit_price: Decimal         # executed exit price (incl. slippage)
    exit_reason: str            # "tp" | "hard_stop" | "unclosed" | "capital_exhausted_stuck"
    realized_pnl: Decimal       # net of fees & slippage; mark-to-market if unclosed


@dataclass(frozen=True)
class BacktestResult:
    symbol: str
    n_cycles: int
    n_tp_cycles: int
    total_pnl: Decimal          # includes mark-to-market of an unclosed final cycle
    total_return_pct: Decimal
    max_safety_orders_used: int
    max_drawdown_pct: Decimal
    ended_stuck: bool           # a cycle was still open / underwater at series end
    cycles: tuple[CycleResult, ...]
    log_path: Optional[Path]


class _OpenCycle:
    """Mutable per-cycle accumulator (engine-internal)."""

    def __init__(self, cycle_id: int, grid: Grid, started_ts: datetime) -> None:
        self.cycle_id = cycle_id
        self.grid = grid
        self.started_ts = started_ts
        self.next_safety_idx = 1          # next safety leg to consider (1..N)
        self.total_quote = Decimal("0")   # USDT committed (pre-fee)
        self.total_base = Decimal("0")    # base asset acquired (post-slippage)
        self.buy_fee = Decimal("0")
        self.legs_filled = 0
        self.last_fill_price = grid.base_price  # executed price of deepest fill
        self.capital_exhausted = False

    @property
    def avg_entry(self) -> Decimal:
        return self.total_quote / self.total_base

    def unrealized(self, price: Decimal) -> Decimal:
        """Mark-to-market PnL at ``price`` (excludes the future sell fee)."""
        if self.total_base <= 0:
            return Decimal("0")
        return self.total_base * price - self.total_quote - self.buy_fee


def run_backtest(
    bars: Sequence[PriceBar],
    params: MartingaleParams,
    config: BacktestConfig,
    log_path: Optional[Path] = None,
) -> BacktestResult:
    if len(bars) < 2:
        raise ValueError("need at least 2 bars to backtest")

    slip = config.slippage
    fee = config.taker_fee
    capital = config.initial_capital

    log_handle = None
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = log_path.open("w")

    def _log(ts: datetime, event: str, side: str, qty: Decimal, price: Decimal, pnl: Decimal) -> None:
        if log_handle is None:
            return
        rec = {
            "ts": ts.isoformat(),
            "source": _SOURCE,
            "event": event,
            "level": "INFO",
            "payload": {
                "symbol": config.symbol,
                "side": side,
                "qty": str(qty),
                "price": str(price),
                "pnl": str(pnl),
            },
        }
        log_handle.write(json.dumps(rec) + "\n")

    def _buy(cycle: _OpenCycle, leg_price: Decimal, quote: Decimal, ts: datetime) -> bool:
        """Fill one buy leg at ``leg_price`` (+slippage). Returns False if unaffordable."""
        if cycle.total_quote + quote > capital:
            cycle.capital_exhausted = True
            return False
        exec_price = leg_price * (Decimal("1") + slip)
        base = quote / exec_price
        cycle.total_quote += quote
        cycle.total_base += base
        cycle.buy_fee += quote * fee
        cycle.legs_filled += 1
        cycle.last_fill_price = exec_price
        _log(ts, "order_filled", "BUY", base, exec_price, Decimal("0"))
        return True

    cycles: list[CycleResult] = []
    realized_pnl = Decimal("0")
    max_safety_used = 0
    peak_equity = capital
    max_dd = Decimal("0")
    cycle_counter = 0

    # Open the first cycle on bar 0 (immediate trigger).
    cycle_counter += 1
    first_cycle = _OpenCycle(cycle_counter, build_grid(bars[0].close, params), bars[0].ts)
    _buy(first_cycle, bars[0].close, params.base_order_size, bars[0].ts)
    open_cycle: Optional[_OpenCycle] = first_cycle
    pending_reopen = False  # set True after a close; next bar opens a fresh cycle

    def _close(cycle: _OpenCycle, exec_price: Decimal, reason: str, ts: datetime) -> CycleResult:
        nonlocal realized_pnl
        gross = cycle.total_base * exec_price
        sell_fee = gross * fee
        pnl = gross - sell_fee - cycle.total_quote - cycle.buy_fee
        realized_pnl += pnl
        _log(ts, "cycle_closed", "SELL", cycle.total_base, exec_price, pnl)
        return CycleResult(
            cycle_id=cycle.cycle_id,
            started_ts=cycle.started_ts,
            ended_ts=ts,
            base_price=cycle.grid.base_price,
            legs_filled=cycle.legs_filled,
            avg_entry=cycle.avg_entry,
            exit_price=exec_price,
            exit_reason=reason,
            realized_pnl=pnl,
        )

    for bar in bars[1:]:
        # 1) Reopen a cycle that was closed on the previous bar (immediate trigger).
        if pending_reopen and open_cycle is None:
            cycle_counter += 1
            open_cycle = _OpenCycle(cycle_counter, build_grid(bar.close, params), bar.ts)
            _buy(open_cycle, bar.close, params.base_order_size, bar.ts)
            pending_reopen = False

        if open_cycle is not None:
            cyc = open_cycle

            # 2) Adverse first: fill any safety orders the dip reached (in order).
            while cyc.next_safety_idx <= params.max_safety_orders:
                leg = cyc.grid.legs[cyc.next_safety_idx]
                if bar.low <= leg.price:
                    if not _buy(cyc, leg.price, leg.quote_size, bar.ts):
                        break  # capital exhausted — cannot go deeper
                    cyc.next_safety_idx += 1
                    max_safety_used = max(max_safety_used, cyc.next_safety_idx - 1)
                else:
                    break

            # 3) Optional hard stop (winner-asymmetry overlay) below the deepest fill.
            closed = False
            if params.hard_stop_pct > 0:
                stop_price = cyc.last_fill_price * (Decimal("1") - params.hard_stop_pct)
                if bar.low <= stop_price:
                    exec_price = stop_price * (Decimal("1") - slip)
                    cycles.append(_close(cyc, exec_price, "hard_stop", bar.ts))
                    open_cycle = None
                    pending_reopen = True
                    closed = True

            # 4) Take-profit on the bounce (recompute trigger after fills).
            if not closed:
                trigger = tp_price(cyc.avg_entry, params.tp_target)
                if bar.high >= trigger:
                    exec_price = trigger * (Decimal("1") - slip)
                    reason = "tp"
                    cycles.append(_close(cyc, exec_price, reason, bar.ts))
                    open_cycle = None
                    pending_reopen = True

        # 5) Mark-to-market equity & drawdown.
        unreal_low = open_cycle.unrealized(bar.low) if open_cycle else Decimal("0")
        unreal_close = open_cycle.unrealized(bar.close) if open_cycle else Decimal("0")
        low_equity = capital + realized_pnl + unreal_low
        close_equity = capital + realized_pnl + unreal_close
        if peak_equity > 0:
            dd = (peak_equity - low_equity) / peak_equity
            if dd > max_dd:
                max_dd = dd
        peak_equity = max(peak_equity, close_equity)

    # End of series: an unclosed cycle is recorded at mark-to-market. It only counts
    # as a "stuck bag" if it is underwater or could not afford its full ladder — a
    # freshly opened, roughly flat cycle (the bot is always in a cycle) is not stuck.
    ended_stuck = False
    if open_cycle is not None:
        last = bars[-1]
        mark = last.close
        mtm = open_cycle.unrealized(mark)
        realized_pnl += mtm
        is_stuck = mtm < 0 or open_cycle.capital_exhausted
        reason = "capital_exhausted_stuck" if open_cycle.capital_exhausted else "unclosed"
        cycles.append(
            CycleResult(
                cycle_id=open_cycle.cycle_id,
                started_ts=open_cycle.started_ts,
                ended_ts=last.ts,
                base_price=open_cycle.grid.base_price,
                legs_filled=open_cycle.legs_filled,
                avg_entry=open_cycle.avg_entry,
                exit_price=mark,
                exit_reason=reason,
                realized_pnl=mtm,
            )
        )
        ended_stuck = is_stuck

    if log_handle is not None:
        log_handle.close()

    n_tp = sum(1 for c in cycles if c.exit_reason == "tp")
    total_return = (realized_pnl / capital) if capital > 0 else Decimal("0")

    return BacktestResult(
        symbol=config.symbol,
        n_cycles=len(cycles),
        n_tp_cycles=n_tp,
        total_pnl=realized_pnl,
        total_return_pct=total_return,
        max_safety_orders_used=max_safety_used,
        max_drawdown_pct=max_dd,
        ended_stuck=ended_stuck,
        cycles=tuple(cycles),
        log_path=log_path,
    )
