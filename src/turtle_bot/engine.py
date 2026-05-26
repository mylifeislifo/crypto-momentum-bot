"""Turtle M1 backtest engine — Donchian breakout, no-lookahead, Decimal accounting.

Ported from the dispatch implementation and rewired onto this package: Polars
(not pandas — trading §7.1) for vectorised indicators, this package's config for
parameters, the video's 2% risk (not the dispatch's 1%), and transaction costs on
every trade (the dispatch run had omitted costs entirely).

Engine rules (README "엔진 설계 결정"):
  - Entry long  : close[t-1] > prior entry-window Donchian high AND close[t-1] > SMA
  - Entry short : close[t-1] < prior entry-window Donchian low  AND close[t-1] < SMA
  - Exit  long  : close[t-1] < prior exit-window Donchian low
  - Exit  short : close[t-1] > prior exit-window Donchian high
  - Stop        : entry ± atr_stop_multiplier × ATR — intrabar touch fills at the stop
  - Sizing      : qty = floor((equity × risk_per_trade) / (ATR × atr_stop_multiplier))

No-lookahead (decision 1): every indicator is shift(1) before its window op, the
signal is decided on bar t-1's close, and the fill is bar t's open.
Direction gate (decision 2): the SMA regime selects long-only above / short-only
below.

Indicators are computed in Float64 (vectorised); fill prices and all money/qty/pnl
arithmetic stay Decimal — OHLC fills come straight from the Decimal cache columns,
so no float round-trip touches a traded price (trading §1.2 / §7.1).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Iterable

import polars as pl

from .config import BacktestConfig, TurtleParams

QTY_STEP = Decimal("0.000001")  # position-size quantisation grain


# ── indicators (no-lookahead: shift(1) before each window op) ───────────────


def precompute(df: pl.DataFrame, params: TurtleParams) -> pl.DataFrame:
    """Return *df* with Float64 indicator columns derived from PRIOR bars only."""
    hi = pl.col("high").cast(pl.Float64)
    lo = pl.col("low").cast(pl.Float64)
    cl = pl.col("close").cast(pl.Float64)
    prev_close = cl.shift(1)
    true_range = pl.max_horizontal(
        hi - lo, (hi - prev_close).abs(), (lo - prev_close).abs()
    )
    return df.with_columns(
        hi.shift(1).rolling_max(window_size=params.entry_window).alias("dc_entry_high"),
        lo.shift(1).rolling_min(window_size=params.entry_window).alias("dc_entry_low"),
        hi.shift(1).rolling_max(window_size=params.exit_window).alias("dc_exit_high"),
        lo.shift(1).rolling_min(window_size=params.exit_window).alias("dc_exit_low"),
        cl.shift(1).rolling_mean(window_size=params.trend_sma_window).alias("sma"),
        # Wilder smoothing, then shift(1) so bar t only sees ATR through t-1.
        true_range.ewm_mean(alpha=1 / params.atr_window, adjust=False)
        .shift(1)
        .alias("atr"),
    )


# ── trade model ─────────────────────────────────────────────────────────────


@dataclass
class OpenPosition:
    symbol: str
    side: str  # "long" | "short"
    qty: Decimal
    entry_ts: datetime
    entry_price: Decimal
    stop: Decimal


@dataclass
class ClosedTrade:
    symbol: str
    side: str
    qty: Decimal
    entry_ts: datetime
    entry_price: Decimal
    exit_ts: datetime
    exit_price: Decimal
    pnl: Decimal  # net of costs
    cost: Decimal
    exit_reason: str

    def as_event(self) -> dict[str, object]:
        """audit-log §2.1 envelope: ts / source / event / level / payload."""
        return {
            "ts": self.exit_ts.isoformat(),
            "source": "turtle_m1",
            "event": "trade_closed",
            "level": "INFO",
            "payload": {
                "symbol": self.symbol,
                "side": self.side,
                "qty": str(self.qty),
                "entry": str(self.entry_price),
                "exit": str(self.exit_price),
                "pnl": str(self.pnl),
                "cost": str(self.cost),
                "entry_ts": self.entry_ts.isoformat(),
                "exit_reason": self.exit_reason,
            },
        }


@dataclass
class BacktestSummary:
    initial_equity: Decimal
    final_equity: Decimal
    trades: list[ClosedTrade] = field(default_factory=list)

    @property
    def n_trades(self) -> int:
        return len(self.trades)

    @property
    def total_return(self) -> Decimal:
        if self.initial_equity == 0:
            return Decimal(0)
        return (self.final_equity - self.initial_equity) / self.initial_equity

    @property
    def total_costs(self) -> Decimal:
        return sum((t.cost for t in self.trades), Decimal(0))

    @property
    def win_rate(self) -> Decimal:
        if not self.trades:
            return Decimal(0)
        wins = sum(1 for t in self.trades if t.pnl > 0)
        return Decimal(wins) / Decimal(len(self.trades))

    def as_summary_dict(self) -> dict[str, object]:
        return {
            "initial_equity": str(self.initial_equity),
            "final_equity": str(self.final_equity),
            "total_return": str(self.total_return),
            "win_rate": str(self.win_rate),
            "n_trades": self.n_trades,
            "total_costs": str(self.total_costs),
        }


# ── helpers ───────────────────────────────────────────────────────────────--


def _floor_qty(qty: Decimal, step: Decimal = QTY_STEP) -> Decimal:
    if step <= 0:
        return qty
    return (qty / step).to_integral_value(rounding=ROUND_DOWN) * step


def _q(price: Decimal) -> Decimal:
    return price.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_EVEN)


def _trade_cost(
    entry_price: Decimal, exit_price: Decimal, qty: Decimal, config: BacktestConfig
) -> Decimal:
    """README cost model: taker fee on both fills + slippage once (round-trip).

    fee  = (entry_notional + exit_notional) × taker_fee   (taker 0.04% × 2 in/out)
    slip = mean_notional × slippage                       (slippage 0.05%)
    """
    entry_notional = entry_price * qty
    exit_notional = exit_price * qty
    fee = (entry_notional + exit_notional) * config.taker_fee
    slip = ((entry_notional + exit_notional) / Decimal(2)) * config.slippage
    return fee + slip


def _gross_pnl(side: str, entry_price: Decimal, exit_price: Decimal, qty: Decimal) -> Decimal:
    sign = Decimal(1) if side == "long" else Decimal(-1)
    return (exit_price - entry_price) * qty * sign


def _close(
    pos: OpenPosition,
    exit_ts: datetime,
    exit_price: Decimal,
    reason: str,
    config: BacktestConfig,
) -> tuple[ClosedTrade, Decimal]:
    gross = _gross_pnl(pos.side, pos.entry_price, exit_price, pos.qty)
    cost = _trade_cost(pos.entry_price, exit_price, pos.qty, config)
    net = gross - cost
    trade = ClosedTrade(
        symbol=pos.symbol,
        side=pos.side,
        qty=pos.qty,
        entry_ts=pos.entry_ts,
        entry_price=pos.entry_price,
        exit_ts=exit_ts,
        exit_price=_q(exit_price),
        pnl=net,
        cost=cost,
        exit_reason=reason,
    )
    return trade, net


# ── engine ───────────────────────────────────────────────────────────────--


def _run_symbol(
    symbol: str,
    df: pl.DataFrame,
    params: TurtleParams,
    config: BacktestConfig,
    equity: Decimal,
) -> tuple[list[ClosedTrade], Decimal]:
    pre = precompute(df, params)
    ts = pre["ts"].to_list()
    op = pre["open"].to_list()  # Decimal (exact, from cache)
    hi = pre["high"].to_list()
    lo = pre["low"].to_list()
    cl = pre["close"].to_list()
    dc_eh = pre["dc_entry_high"].to_list()
    dc_el = pre["dc_entry_low"].to_list()
    dc_xh = pre["dc_exit_high"].to_list()
    dc_xl = pre["dc_exit_low"].to_list()
    sma = pre["sma"].to_list()
    atr = pre["atr"].to_list()

    trades: list[ClosedTrade] = []
    position: OpenPosition | None = None
    n = len(df)

    for i in range(1, n):
        j = i - 1  # the "previous bar" the signal is decided on

        if position is not None:
            if position.side == "long" and lo[i] <= position.stop:
                trade, net = _close(position, ts[i], position.stop, "stop", config)
                equity += net
                trades.append(trade)
                position = None
            elif position.side == "short" and hi[i] >= position.stop:
                trade, net = _close(position, ts[i], position.stop, "stop", config)
                equity += net
                trades.append(trade)
                position = None
            elif position.side == "long" and dc_xl[j] is not None and cl[j] < dc_xl[j]:
                trade, net = _close(position, ts[i], op[i], "donchian", config)
                equity += net
                trades.append(trade)
                position = None
            elif position.side == "short" and dc_xh[j] is not None and cl[j] > dc_xh[j]:
                trade, net = _close(position, ts[i], op[i], "donchian", config)
                equity += net
                trades.append(trade)
                position = None

        if position is None:
            signal = _entry_signal(cl[j], sma[j], dc_eh[j], dc_el[j])
            if signal is not None and atr[j] is not None and atr[j] > 0:
                stop_dist = Decimal(str(atr[j])) * params.atr_stop_multiplier
                if stop_dist > 0:
                    qty = _floor_qty(equity * params.risk_per_trade / stop_dist)
                    entry_px = op[i]
                    max_notional = equity * config.leverage
                    if entry_px > 0 and entry_px * qty > max_notional:
                        qty = _floor_qty(max_notional / entry_px)
                    if qty > 0:
                        stop_px = (
                            entry_px - stop_dist if signal == "long" else entry_px + stop_dist
                        )
                        position = OpenPosition(
                            symbol=symbol,
                            side=signal,
                            qty=qty,
                            entry_ts=ts[i],
                            entry_price=_q(entry_px),
                            stop=_q(stop_px),
                        )

    if position is not None:
        trade, net = _close(position, ts[n - 1], cl[n - 1], "end_of_data", config)
        equity += net
        trades.append(trade)

    return trades, equity


def _entry_signal(
    close: Decimal, sma: float | None, dc_high: float | None, dc_low: float | None
) -> str | None:
    if sma is None or dc_high is None or dc_low is None:
        return None
    if close > dc_high and close > sma:
        return "long"
    if close < dc_low and close < sma:
        return "short"
    return None


def run_backtest(
    bars: dict[str, pl.DataFrame],
    params: TurtleParams,
    config: BacktestConfig,
) -> BacktestSummary:
    """Run the M1 engine per symbol with independent equity slices (50/50)."""
    initial_equity = config.initial_capital
    if not bars:
        return BacktestSummary(initial_equity, initial_equity)

    per_symbol_equity = initial_equity / Decimal(len(bars))
    closed: list[ClosedTrade] = []
    final_equity = Decimal(0)

    for symbol, df in bars.items():
        trades, equity = _run_symbol(symbol, df, params, config, per_symbol_equity)
        closed.extend(trades)
        final_equity += equity

    return BacktestSummary(initial_equity=initial_equity, final_equity=final_equity, trades=closed)


def write_trades_jsonl(trades: Iterable[ClosedTrade], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for trade in trades:
            f.write(json.dumps(trade.as_event(), ensure_ascii=False) + "\n")
            n += 1
    return n
