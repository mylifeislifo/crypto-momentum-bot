"""Backtest orchestrator. Wires data → indicators → strategy → risk → portfolio
→ backtest gateway, then computes metrics on the resulting equity curve.

Reuses live code paths: same Strategy, same allocator, same Portfolio, same
fill callback. Only the gateway differs.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd

from bot.config.schema import AppConfig
from bot.core.enums import Interval
from bot.core.logging import get_logger
from bot.core.types import Fill
from bot.data import cache as ohlcv_cache
from bot.data.indicators import add_all
from bot.gateway.backtest import BacktestGateway
from bot.portfolio.allocator import allocate
from bot.portfolio.portfolio import Portfolio
from bot.risk.guard import Guard
from bot.risk.stops import StopReason, check_stops, update_trail
from bot.strategy.base import StrategyContext
from bot.strategy.momentum import MomentumTrendStrategy
from bot.strategy.regime import RegimeFilter

from .metrics import Metrics, compute

log = get_logger(__name__)

_BARS_PER_YEAR = {
    Interval.M1: 365.25 * 24 * 60,
    Interval.M5: 365.25 * 24 * 12,
    Interval.M15: 365.25 * 24 * 4,
    Interval.M60: 365.25 * 24,
    Interval.M240: 365.25 * 6,
    Interval.D1: 365.25,
}


@dataclass
class BacktestResult:
    metrics: Metrics
    equity_curve: pd.Series
    trades: list[dict]

    def summary(self) -> str:
        m = self.metrics.as_dict()
        lines = [
            f"trades={m['n_trades']:>4}  total_return={m['total_return']:.2%}  "
            f"CAGR={m['cagr']:.2%}  Sharpe={m['sharpe']:.2f}  Sortino={m['sortino']:.2f}",
            f"MDD={m['max_drawdown']:.2%}  Calmar={m['calmar']:.2f}  "
            f"WinRate={m['win_rate']:.2%}  PF={m['profit_factor']:.2f}  "
            f"Expectancy(R)={m['expectancy_r']:.2f}",
            f"AvgHoldHrs={m['avg_holding_hours']:.1f}  Exposure={m['exposure']:.2%}",
        ]
        return "\n".join(lines)


def _load_symbol_bars(
    cache_dir: Path, interval: Interval, symbols: list[str], start: datetime, end: datetime,
    params: dict,
) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for s in symbols:
        df = ohlcv_cache.read(cache_dir, interval, s)
        if df.empty:
            continue
        s_ts = pd.Timestamp(start) if start.tzinfo else pd.Timestamp(start, tz="UTC")
        e_ts = pd.Timestamp(end) if end.tzinfo else pd.Timestamp(end, tz="UTC")
        df = df[(df.index >= s_ts) & (df.index <= e_ts)]
        if df.empty:
            continue
        out[s] = add_all(df, params)
    return out


def _load_regime(cache_dir: Path, cfg: AppConfig) -> RegimeFilter:
    df = ohlcv_cache.read(cache_dir, cfg.regime.source_interval, cfg.regime.source_symbol)
    return RegimeFilter(df, cfg.regime.ema_fast, cfg.regime.ema_slow)


def run_backtest(cfg: AppConfig, symbols: list[str] | None = None) -> BacktestResult:
    start = datetime.fromisoformat(cfg.backtest.start).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(cfg.backtest.end).replace(tzinfo=timezone.utc)

    # Discover symbols from cache if not given
    interval = cfg.data.interval
    if symbols is None:
        cache_root = cfg.cache_path() / interval.value
        if not cache_root.exists():
            raise RuntimeError(f"No cached data at {cache_root}; run `bot fetch` first")
        symbols = sorted(p.stem for p in cache_root.glob("*.parquet"))
    if not symbols:
        raise RuntimeError("No symbols available for backtest")

    bars = _load_symbol_bars(cfg.cache_path(), interval, symbols, start, end, cfg.strategy.params.model_dump())
    if not bars:
        raise RuntimeError("No bars loaded for any symbol in the requested window")

    regime = _load_regime(cfg.cache_path(), cfg)
    gateway = BacktestGateway(
        bars=bars,
        fee_per_side=cfg.execution.fee_per_side,
        slippage_bps=cfg.execution.slippage_bps,
        starting_cash=Decimal(str(cfg.backtest.initial_equity)),
    )
    portfolio = Portfolio(quote="KRW", cash=Decimal(str(cfg.backtest.initial_equity)))
    strategy = MomentumTrendStrategy(cfg.strategy.params.model_dump())
    guard = Guard(
        daily_loss_limit=cfg.risk.daily_loss_limit,
        weekly_loss_limit=cfg.risk.weekly_loss_limit,
        mdd_killswitch=cfg.risk.mdd_killswitch,
    )

    pending_stops: dict[str, Decimal] = {}  # symbol -> initial_stop for next BUY fill

    def on_fill(fill: Fill) -> None:
        stop = pending_stops.pop(fill.symbol, None)
        portfolio.apply_fill(fill, initial_stop=stop)

    gateway.on_fill(on_fill)
    metas = {s: gateway.symbol_meta(s) for s in bars}

    last_marks: dict[str, Decimal] = {}

    def on_bar(symbol: str, bar: pd.Series) -> None:
        last_marks[symbol] = Decimal(str(bar["close"]))
        ts = bar.name.to_pydatetime() if hasattr(bar.name, "to_pydatetime") else bar.name

        # Update trail high-watermark and check risk-based stops first
        pos = portfolio.positions.get(symbol)
        if pos is not None and not pd.isna(bar.get("atr")):
            atr_v = Decimal(str(bar["atr"]))
            high = Decimal(str(bar["high"]))
            if high > pos.high_watermark:
                pos.high_watermark = high
            update_trail(pos, atr_v, cfg.risk.trail_atr_mult)
            reason = check_stops(
                pos,
                bar_low=Decimal(str(bar["low"])),
                bar_close=Decimal(str(bar["close"])),
                now=ts,
                time_stop_hours=cfg.risk.time_stop_hours,
            )
            if reason is not StopReason.NONE:
                from bot.core.enums import OrderSide, OrderType
                from bot.core.types import Order
                gateway.place_order(Order(
                    symbol=symbol, side=OrderSide.SELL, type=OrderType.MARKET,
                    qty=pos.qty, quote_currency="KRW",
                ))
                return  # don't also evaluate strategy this bar

        # Strategy signals
        ctx = StrategyContext(
            ts=ts,
            symbol=symbol,
            bar=bar,
            history=bars[symbol].loc[:ts],
            position=portfolio.positions.get(symbol),
            regime=regime.at(ts),
            last_exit_ts=portfolio.last_exit_ts.get(symbol),
        )
        sigs = strategy.on_bar(ctx)
        # attach entry_price for the allocator
        for s in sigs:
            if s.enter:
                s.meta["entry_price"] = float(bar["close"])

        # Update guard with current equity
        equity = portfolio.equity(last_marks)
        guard.update(ts, equity)
        if guard.must_liquidate():
            for sym, p in list(portfolio.positions.items()):
                from bot.core.enums import OrderSide, OrderType
                from bot.core.types import Order
                gateway.place_order(Order(
                    symbol=sym, side=OrderSide.SELL, type=OrderType.MARKET,
                    qty=p.qty, quote_currency="KRW",
                ))
            return

        allocs = allocate(
            signals=sigs,
            portfolio=portfolio,
            risk=cfg.risk,
            metas=metas,
            equity=equity,
            can_enter=guard.can_enter(),
        )
        for a in allocs:
            from bot.core.enums import OrderSide
            if a.order.side is OrderSide.BUY:
                pending_stops[a.order.symbol] = a.initial_stop
            gateway.place_order(a.order)

        # Snapshot equity curve at this bar
        portfolio.snapshot(ts, last_marks)

    log.info("backtest_start", symbols=list(bars), bars=sum(len(d) for d in bars.values()))
    gateway.subscribe_bars(list(bars), interval, on_bar)
    log.info("backtest_done", trades=len(portfolio.trades))

    eq_curve = pd.Series(
        [float(s.equity) for s in portfolio.equity_curve],
        index=pd.DatetimeIndex([s.ts for s in portfolio.equity_curve], tz="UTC"),
    )
    bpy = _BARS_PER_YEAR.get(interval, 365.25)
    metrics = compute(eq_curve, portfolio.trades, bars_per_year=bpy)
    return BacktestResult(metrics=metrics, equity_curve=eq_curve, trades=portfolio.trades)
