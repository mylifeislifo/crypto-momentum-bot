"""Quick connectivity check: runs all 3 data pipelines for N seconds,
then prints queue depths and sample messages.

Usage:
  python scripts/check_connectivity.py [--duration 60] [--config config/default.yaml]
"""

import asyncio
import sys
from pathlib import Path

# allow running from project root
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import argparse
import logging

from bot.config.loader import load_config
from bot.core.logging import setup_logging
from bot.data import bar_builder as _bar_builder_mod
from bot.data import pipeline_oi_funding, pipeline_orderbook, pipeline_sentiment
from bot.data.bar_builder import BarBuilder
from bot.core.enums import Interval


async def main(duration: int, config_path: str) -> None:
    config, secrets = load_config(config_path)
    setup_logging(config.logging.level, json_format=False)
    log = logging.getLogger("check_connectivity")

    ob_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
    trade_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
    oi_queue: asyncio.Queue = asyncio.Queue(maxsize=60)
    sentiment_queue: asyncio.Queue = asyncio.Queue(maxsize=24)
    bar_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    notify_queue: asyncio.Queue = asyncio.Queue(maxsize=100)

    builder = BarBuilder(intervals=[Interval.M5, Interval.M15])

    tasks = [
        asyncio.create_task(
            pipeline_orderbook.run(ob_queue, trade_queue, notify_queue, config.exchange.symbol),
            name="orderbook",
        ),
        asyncio.create_task(
            pipeline_oi_funding.run(oi_queue, notify_queue, config.exchange.symbol, config.data.oi_poll_sec),
            name="oi_funding",
        ),
        asyncio.create_task(
            pipeline_sentiment.run(sentiment_queue, notify_queue, secrets.coinglass_api_key, config.data.sentiment_poll_sec),
            name="sentiment",
        ),
        asyncio.create_task(
            builder.run(trade_queue, bar_queue),
            name="bar_builder",
        ),
    ]

    log.info(f"Running connectivity check for {duration}s...")

    await asyncio.sleep(duration)

    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    print("\n" + "=" * 60)
    print("CONNECTIVITY CHECK RESULTS")
    print("=" * 60)
    print(f"  ob_queue depth     : {ob_queue.qsize()}")
    print(f"  trade_queue depth  : {trade_queue.qsize()}")
    print(f"  oi_queue depth     : {oi_queue.qsize()}")
    print(f"  sentiment_queue    : {sentiment_queue.qsize()}")
    print(f"  bar_queue depth    : {bar_queue.qsize()}")
    print(f"  notify_queue depth : {notify_queue.qsize()}")
    print()

    if not ob_queue.empty():
        snap = ob_queue.get_nowait()
        print(f"  Latest OB snapshot : mid={snap.mid_price}, imbalance={snap.imbalance:.4f}, spread={snap.spread}")

    if not oi_queue.empty():
        oi = oi_queue.get_nowait()
        print(f"  Latest OI/Funding  : OI={oi.open_interest}, delta={oi.oi_delta_pct:.4%}, funding={oi.funding_rate:.4%}")

    if not sentiment_queue.empty():
        sent = sentiment_queue.get_nowait()
        print(f"  Latest Sentiment   : F&G={sent.fear_greed_index} ({sent.sentiment_label.value}), L/S={sent.long_ratio:.2%}/{sent.short_ratio:.2%}")

    if not bar_queue.empty():
        bar = bar_queue.get_nowait()
        print(f"  Latest Bar         : {bar.interval.value} O={bar.open} H={bar.high} L={bar.low} C={bar.close} CVD={bar.cvd_delta:.2f}")

    if not notify_queue.empty():
        print(f"\n  ALERTS ({notify_queue.qsize()} in queue):")
        while not notify_queue.empty():
            evt = notify_queue.get_nowait()
            print(f"    [{evt.event_type.value}] {evt.message}")

    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=30, help="Seconds to run (default: 30)")
    parser.add_argument("--config", default="config/default.yaml", help="Config file path")
    args = parser.parse_args()

    asyncio.run(main(args.duration, args.config))
