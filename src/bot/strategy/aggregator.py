"""Strategy aggregator.

Runs as a single asyncio task. Drains all four input queues on every cycle
without blocking on any single one (non-blocking get_nowait). The 5m bar
arrival is the primary evaluation trigger.

Queue topology (all reads, no writes except signal_queue):
  ob_queue        → ctx.latest_ob
  oi_queue        → ctx.latest_oi
  sentiment_queue → ctx.latest_sentiment
  bar_queue       → ctx.recent_bars_5m / ctx.recent_bars_15m

On each cycle where a new 5m bar was just received AND ctx.is_ready():
  → calls confluence.evaluate(ctx) → puts Signal to signal_queue (if non-None)
"""

import asyncio
import logging

from ..config.schema import AppConfig
from ..core.enums import Interval
from .base import StrategyContext
from .confluence import ConfluenceStrategy

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SEC = 0.10   # 100ms drain cadence (matches OB snapshot rate)
_MIN_BARS_READY = 3         # minimum 5m bars before evaluating


async def run(
    ob_queue: asyncio.Queue,
    oi_queue: asyncio.Queue,
    sentiment_queue: asyncio.Queue,
    bar_queue: asyncio.Queue,
    signal_queue: asyncio.Queue,
    config: AppConfig,
) -> None:
    ctx = StrategyContext()
    engine = ConfluenceStrategy(config.strategy, config.risk)
    min_bars = config.strategy.cvd_lookback_bars

    logger.info("aggregator.started")

    while True:
        try:
            await asyncio.sleep(_POLL_INTERVAL_SEC)

            # --- drain OB (keep only latest; discard stale snapshots) ---
            latest_ob = None
            while not ob_queue.empty():
                try:
                    latest_ob = ob_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            if latest_ob is not None:
                ctx.latest_ob = latest_ob

            # --- drain OI/funding (keep latest) ---
            while not oi_queue.empty():
                try:
                    ctx.latest_oi = oi_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

            # --- drain sentiment (keep latest) ---
            while not sentiment_queue.empty():
                try:
                    ctx.latest_sentiment = sentiment_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

            # --- drain bars (append all new bars; track if 5m arrived) ---
            new_5m_bar = False
            while not bar_queue.empty():
                try:
                    bar = bar_queue.get_nowait()
                    ctx.ingest_bar(bar)
                    if bar.interval == Interval.M5:
                        new_5m_bar = True
                        logger.debug(
                            "aggregator.new_bar",
                            interval="5m",
                            ts=bar.ts.isoformat(),
                            cvd_delta=round(bar.cvd_delta, 4),
                            cvd_cumulative=round(bar.cvd_cumulative, 4),
                        )
                except asyncio.QueueEmpty:
                    break

            # --- evaluate only when a fresh 5m bar arrived ---
            if not new_5m_bar:
                continue
            if not ctx.is_ready(min_bars):
                logger.debug(
                    "aggregator.not_ready",
                    has_ob=ctx.latest_ob is not None,
                    has_oi=ctx.latest_oi is not None,
                    has_sentiment=ctx.latest_sentiment is not None,
                    bars_5m=len(ctx.recent_bars_5m),
                )
                continue

            signal = engine.evaluate(ctx)
            if signal is not None:
                try:
                    signal_queue.put_nowait(signal)
                    logger.info(
                        "aggregator.signal_emitted",
                        side=signal.side.value,
                        entry=str(signal.entry_price_est),
                        stop=str(signal.stop_price),
                        confidence=signal.confidence,
                    )
                except asyncio.QueueFull:
                    logger.warning("aggregator.signal_queue_full")

        except asyncio.CancelledError:
            logger.info("aggregator.cancelled")
            return
        except Exception as exc:
            logger.error("aggregator.unhandled_error", error=str(exc), exc_info=True)
            await asyncio.sleep(1.0)  # brief pause before retrying
