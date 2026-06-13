"""Main entry point — asyncio task graph.

Queue topology:
  ob_queue          (maxsize=1000)  pipeline_orderbook → aggregator, order_manager
  trade_queue       (maxsize=1000)  pipeline_orderbook → bar_builder
  oi_queue          (maxsize=60)    pipeline_oi_funding → aggregator
  sentiment_queue   (maxsize=24)    pipeline_sentiment → aggregator
  bar_queue         (maxsize=200)   bar_builder → aggregator
  trail_bar_queue   (maxsize=200)   aggregator → order_manager
  signal_queue      (maxsize=10)    aggregator → order_manager
  notify_queue      (maxsize=100)   order_manager → telegram

Startup sequence:
  1. Load config + secrets
  2. Setup logging
  3. Create gateway (paper or live)
  4. Set leverage=2 + margin_mode=ISOLATED on exchange
  5. Launch all asyncio tasks in TaskGroup
  6. On SIGTERM/SIGINT: cancel tasks → close_all_positions → flush notify

"""

import asyncio
import signal
import sys
from pathlib import Path
from typing import Optional

import structlog

from .config.loader import load_config
from .config.schema import AppConfig, Secrets
from .core.enums import Exchange, Interval, MarginType, Mode
from .core.logging import setup_logging
from .data import bar_builder as _bar_mod
from .data import pipeline_oi_funding, pipeline_orderbook, pipeline_sentiment
from .data.bar_builder import BarBuilder
from .execution.binance_futures import BinanceFuturesGateway
from .execution.bitget_futures import BitgetFuturesGateway
from .execution.gateway_base import FuturesGateway
from .execution.order_manager import OrderManager
from .execution.paper_futures import PaperFuturesGateway
from .notifications import telegram
from .risk.guard import RiskGuard
from .risk.trail import TrailingStopManager
from .strategy import aggregator

logger = structlog.get_logger(__name__)


def _build_gateway(config: AppConfig, secrets: Secrets) -> FuturesGateway:
    if config.mode == Mode.PAPER:
        return PaperFuturesGateway(initial_balance=config.risk.risk_per_trade and __import__('decimal').Decimal("10000"))
    if config.exchange.name == Exchange.BITGET:
        return BitgetFuturesGateway(
            api_key=secrets.bitget_api_key,
            secret_key=secrets.bitget_secret_key,
            passphrase=secrets.bitget_passphrase,
            product_type=config.exchange.product_type,
            margin_coin=config.exchange.margin_coin,
        )
    return BinanceFuturesGateway(
        api_key=secrets.binance_api_key,
        secret_key=secrets.binance_secret_key,
    )


async def main(config_path: str = "config/default.yaml", override_path: Optional[str] = None) -> None:
    config, secrets = load_config(config_path, override_path)
    setup_logging(config.logging.level, config.logging.json_format)

    log = structlog.get_logger("main")
    log.info("bot.starting", mode=config.mode.value, symbol=config.exchange.symbol)

    # --- build gateway ---
    gateway = _build_gateway(config, secrets)
    await gateway.connect()   # no-op for paper; opens the REST/WS session for live

    # --- startup: set leverage + margin mode ---
    try:
        await gateway.set_leverage(config.exchange.symbol, config.exchange.max_leverage)
        await gateway.set_margin_mode(config.exchange.symbol, config.exchange.margin_mode)
    except Exception as exc:
        log.error("bot.startup_init_failed", error=str(exc))
        if not config.dry_run:
            raise

    # --- create queues ---
    ob_queue:         asyncio.Queue = asyncio.Queue(maxsize=1000)
    trade_queue:      asyncio.Queue = asyncio.Queue(maxsize=1000)
    oi_queue:         asyncio.Queue = asyncio.Queue(maxsize=60)
    sentiment_queue:  asyncio.Queue = asyncio.Queue(maxsize=24)
    bar_queue:        asyncio.Queue = asyncio.Queue(maxsize=200)
    trail_bar_queue:  asyncio.Queue = asyncio.Queue(maxsize=200)
    signal_queue:     asyncio.Queue = asyncio.Queue(maxsize=10)
    notify_queue:     asyncio.Queue = asyncio.Queue(maxsize=config.notifications.queue_max)

    # --- create shared modules ---
    guard = RiskGuard(config.risk, config.exchange)
    trail = TrailingStopManager(
        atr_multiplier=config.risk.trail_atr_multiplier,
        atr_period=config.risk.trail_lookback_bars,
        breakeven_trigger_pct=config.risk.breakeven_trigger_pct,
        breakeven_offset_pct=config.risk.breakeven_offset_pct,
        time_stop_bars=config.risk.time_stop_bars,
        max_hold_bars=config.risk.max_hold_bars,
        state_file=Path("trail_state.json"),   # persist trail state across restarts
    )
    bar_builder = BarBuilder(intervals=[Interval.M5, Interval.M15])
    order_mgr = OrderManager(gateway, guard, trail, notify_queue, config)

    # --- shutdown flag ---
    shutdown_event = asyncio.Event()

    def _handle_signal(sig: int, *_) -> None:
        log.info("bot.shutdown_signal_received", signal=sig)
        shutdown_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal, sig)

    # --- launch tasks ---
    tasks: list[asyncio.Task] = [
        asyncio.create_task(
            pipeline_orderbook.run(ob_queue, trade_queue, notify_queue, config.exchange.symbol,
                                   config.strategy.spoof_size_multiplier),
            name="pipeline_orderbook",
        ),
        asyncio.create_task(
            pipeline_oi_funding.run(oi_queue, notify_queue, config.exchange.symbol, config.data.oi_poll_sec),
            name="pipeline_oi_funding",
        ),
        asyncio.create_task(
            pipeline_sentiment.run(sentiment_queue, notify_queue, secrets.coinglass_api_key,
                                   config.data.sentiment_poll_sec),
            name="pipeline_sentiment",
        ),
        asyncio.create_task(
            bar_builder.run(trade_queue, bar_queue),
            name="bar_builder",
        ),
        asyncio.create_task(
            aggregator.run(ob_queue, oi_queue, sentiment_queue, bar_queue,
                           signal_queue, config, trail_bar_queue=trail_bar_queue),
            name="aggregator",
        ),
        asyncio.create_task(
            order_mgr.run(signal_queue, trail_bar_queue, ob_queue),
            name="order_manager",
        ),
        asyncio.create_task(
            telegram.run(notify_queue, secrets.telegram_bot_token, secrets.telegram_chat_id,
                         config.notifications.rate_limit_sec),
            name="telegram",
        ),
    ]

    log.info("bot.all_tasks_launched", count=len(tasks))

    # --- wait for shutdown or any task crash ---
    try:
        done, pending = await asyncio.wait(
            [asyncio.create_task(shutdown_event.wait()), *tasks],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in done:
            if t.get_name() != "Task-1" and not t.cancelled():
                exc = t.exception()
                if exc:
                    log.error("bot.task_crashed", task=t.get_name(), error=str(exc))
    finally:
        log.info("bot.shutting_down")

        # cancel all running tasks
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        # Hold positions across a restart (winner-asymmetry) unless explicitly told to
        # flatten. Trail state is persisted, the server-side SL protects during downtime,
        # and recover_positions() resumes management on the next start.
        if config.risk.flatten_on_shutdown:
            try:
                fills = await gateway.close_all_positions(config.exchange.symbol)
                if fills:
                    log.info("bot.positions_closed_on_shutdown", count=len(fills))
            except Exception as exc:
                log.error("bot.shutdown_close_failed", error=str(exc))
        else:
            log.info("bot.positions_held_across_restart")

        await gateway.disconnect()   # no-op for paper

        log.info("bot.stopped")
