"""Paper/live runner. Reuses Strategy/Risk/Portfolio modules; gateway is swapped."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd

from bot.config.schema import AppConfig
from bot.core.enums import Interval, Mode, OrderSide, OrderType
from bot.core.logging import get_logger
from bot.core.types import Fill, Order
from bot.data import cache as ohlcv_cache
from bot.data.indicators import add_all
from bot.data.universe import select_universe
from bot.execution.router import OrderRouter
from bot.gateway.base import ExchangeGateway
from bot.gateway.paper import PaperGateway
from bot.portfolio.allocator import allocate
from bot.portfolio.portfolio import Portfolio
from bot.risk.guard import Guard
from bot.risk.stops import StopReason, check_stops, update_trail
from bot.strategy.base import StrategyContext
from bot.strategy.momentum import MomentumTrendStrategy
from bot.strategy.regime import RegimeFilter

from .scheduler import loop as scheduler_loop

log = get_logger(__name__)


def _build_gateway(cfg: AppConfig) -> ExchangeGateway:
    if cfg.mode is Mode.PAPER:
        return PaperGateway(
            starting_cash_krw=Decimal(str(cfg.backtest.initial_equity)),
            fee_per_side=cfg.execution.fee_per_side,
            slippage_bps=cfg.execution.slippage_bps,
            state_path=Path(cfg.logging.dir) / "paper_state.json",
        )
    if cfg.mode is Mode.LIVE:
        from bot.gateway.upbit import UpbitGateway

        return UpbitGateway(
            access_key=cfg.upbit_access_key,
            secret_key=cfg.upbit_secret_key,
            fee_per_side=cfg.execution.fee_per_side,
            slippage_bps=cfg.execution.slippage_bps,
            dry_run=cfg.dry_run,
        )
    raise ValueError(f"_build_gateway: unsupported mode {cfg.mode}")


def _fetch_recent_bars(symbol: str, interval: Interval, lookback: int) -> pd.DataFrame:
    import pyupbit

    df = pyupbit.get_ohlcv(symbol, interval=interval.value, count=lookback)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df[["open", "high", "low", "close", "volume"]].copy()
    df.index = pd.to_datetime(df.index).tz_localize("Asia/Seoul").tz_convert("UTC")
    return df.sort_index()


def run_live(cfg: AppConfig, max_iterations: int | None = None) -> None:
    gateway = _build_gateway(cfg)
    portfolio = Portfolio(quote="KRW", cash=Decimal(str(cfg.backtest.initial_equity)))
    strategy = MomentumTrendStrategy(cfg.strategy.params.model_dump())
    guard = Guard(
        daily_loss_limit=cfg.risk.daily_loss_limit,
        weekly_loss_limit=cfg.risk.weekly_loss_limit,
        mdd_killswitch=cfg.risk.mdd_killswitch,
    )
    router = OrderRouter(gateway, cfg.execution.retry_max, cfg.execution.retry_backoff_sec)

    pending_stops: dict[str, Decimal] = {}

    def on_fill(fill: Fill) -> None:
        stop = pending_stops.pop(fill.symbol, None)
        portfolio.apply_fill(fill, initial_stop=stop)
        log.info("fill", symbol=fill.symbol, side=fill.side.value,
                 qty=str(fill.qty), price=str(fill.price), fee=str(fill.fee))

    gateway.on_fill(on_fill)

    universe = select_universe(cfg)
    metas = {s: gateway.symbol_meta(s) for s in universe}
    log.info("live_start", mode=cfg.mode.value, universe=universe, dry_run=cfg.dry_run)

    last_universe_refresh = datetime.now(timezone.utc)

    def tick(target: datetime) -> None:
        nonlocal last_universe_refresh, universe, metas
        # Refresh universe on schedule
        if (target - last_universe_refresh).total_seconds() / 3600 >= cfg.universe.refresh_hours:
            universe = select_universe(cfg)
            metas = {s: gateway.symbol_meta(s) for s in universe}
            last_universe_refresh = target

        # 1) Settle paper-pending fills against current orderbook
        if isinstance(gateway, PaperGateway):
            gateway.settle_pending()

        # 2) Pull recent bars + regime, then evaluate per symbol
        regime_df = _fetch_recent_bars(cfg.regime.source_symbol, cfg.regime.source_interval, 250)
        regime = RegimeFilter(regime_df, cfg.regime.ema_fast, cfg.regime.ema_slow)

        marks: dict[str, Decimal] = {}
        for symbol in universe:
            df = _fetch_recent_bars(symbol, cfg.data.interval, 200)
            if df.empty or len(df) < cfg.strategy.params.ema_slow + 5:
                continue
            df = add_all(df, cfg.strategy.params.model_dump())
            bar = df.iloc[-1]
            marks[symbol] = Decimal(str(bar["close"]))

            # Risk-based stop check (intra-bar approximated by last bar low)
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
                    now=target,
                    time_stop_hours=cfg.risk.time_stop_hours,
                )
                if reason is not StopReason.NONE:
                    log.info("stop_triggered", symbol=symbol, reason=reason.value)
                    router.submit(Order(
                        symbol=symbol, side=OrderSide.SELL, type=OrderType.MARKET,
                        qty=pos.qty, quote_currency="KRW",
                    ))
                    continue

            ctx = StrategyContext(
                ts=target,
                symbol=symbol,
                bar=bar,
                history=df,
                position=portfolio.positions.get(symbol),
                regime=regime.at(target),
                last_exit_ts=portfolio.last_exit_ts.get(symbol),
            )
            sigs = strategy.on_bar(ctx)
            for s in sigs:
                if s.enter:
                    s.meta["entry_price"] = float(bar["close"])

            equity = portfolio.equity(marks)
            guard.update(target, equity)
            if guard.must_liquidate():
                log.error("kill_switch", equity=str(equity))
                for sym, p in list(portfolio.positions.items()):
                    router.submit(Order(
                        symbol=sym, side=OrderSide.SELL, type=OrderType.MARKET,
                        qty=p.qty, quote_currency="KRW",
                    ))
                return

            allocs = allocate(
                signals=sigs, portfolio=portfolio, risk=cfg.risk,
                metas=metas, equity=equity, can_enter=guard.can_enter(),
            )
            for a in allocs:
                if a.order.side is OrderSide.BUY:
                    pending_stops[a.order.symbol] = a.initial_stop
                router.submit(a.order)

        snap = portfolio.snapshot(target, marks)
        log.info("tick_summary", ts=target.isoformat(),
                 equity=str(snap.equity), cash=str(snap.cash),
                 positions=len(portfolio.positions))

    scheduler_loop(cfg.data.interval, tick, max_iterations=max_iterations)
