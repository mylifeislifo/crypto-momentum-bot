"""Pipeline A: Binance Futures WebSocket — L2 orderbook + aggTrade stream.

Subscribes to a combined stream:
  - btcusdt@depth20@100ms  → every 100ms, full top-20 snapshot
  - btcusdt@aggTrade       → every trade (real-time)

Outputs:
  - ob_queue    : OBSnapshot  (every 100ms)
  - trade_queue : Trade        (every aggTrade event)

Reconnection: exponential backoff up to MAX_BACKOFF_SEC on any error.
Sends a Telegram CRITICAL alert if disconnected for > ALERT_DISCONNECT_SEC.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal

import websockets
from websockets.exceptions import ConnectionClosed

from ..core.types import OBLevel, OBSnapshot, NotifyEvent, Trade
from ..core.enums import NotifyEventType
from ..core.clock import utc_now
from .spoof_filter import compute_imbalance, filter_spoofs

logger = logging.getLogger(__name__)

_WS_BASE = "wss://fstream.binance.com/stream"
_STREAMS = "btcusdt@depth20@100ms/btcusdt@aggTrade"
_WS_URL = f"{_WS_BASE}?streams={_STREAMS}"

MAX_BACKOFF_SEC = 60.0
ALERT_DISCONNECT_SEC = 30.0


async def run(
    ob_queue: asyncio.Queue,
    trade_queue: asyncio.Queue,
    notify_queue: asyncio.Queue,
    symbol: str = "BTCUSDT",
    spoof_multiplier: float = 3.0,
) -> None:
    """Main entry point — runs forever, reconnecting on errors."""
    backoff = 1.0
    disconnect_start: float | None = None

    while True:
        try:
            logger.info("orderbook_pipeline.connecting", url=_WS_URL)
            async with websockets.connect(
                _WS_URL,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5,
            ) as ws:
                logger.info("orderbook_pipeline.connected")
                backoff = 1.0
                disconnect_start = None

                async for raw in ws:
                    msg = json.loads(raw)
                    stream = msg.get("stream", "")
                    data = msg.get("data", {})

                    if "@depth20" in stream:
                        _handle_depth(data, ob_queue, spoof_multiplier)
                    elif "aggTrade" in stream:
                        _handle_agg_trade(data, trade_queue)

        except (ConnectionClosed, OSError, asyncio.TimeoutError) as exc:
            if disconnect_start is None:
                disconnect_start = asyncio.get_event_loop().time()

            elapsed = asyncio.get_event_loop().time() - disconnect_start
            logger.warning("orderbook_pipeline.disconnected", error=str(exc), elapsed_sec=round(elapsed, 1))

            if elapsed >= ALERT_DISCONNECT_SEC:
                await _alert(notify_queue, f"WebSocket disconnected for {elapsed:.0f}s — reconnecting")
                disconnect_start = None  # reset so we don't spam

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_SEC)

        except asyncio.CancelledError:
            logger.info("orderbook_pipeline.cancelled")
            return


def _handle_depth(data: dict, ob_queue: asyncio.Queue, spoof_multiplier: float) -> None:
    ts_ms = data.get("T") or data.get("E")
    ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc) if ts_ms else utc_now()

    raw_bids = [OBLevel(price=Decimal(p), qty=Decimal(q)) for p, q in data.get("b", [])]
    raw_asks = [OBLevel(price=Decimal(p), qty=Decimal(q)) for p, q in data.get("a", [])]

    # sort: bids desc (best bid first), asks asc (best ask first)
    raw_bids.sort(key=lambda l: l.price, reverse=True)
    raw_asks.sort(key=lambda l: l.price)

    # remove zero-qty levels (order book deletions)
    raw_bids = [l for l in raw_bids if l.qty > 0]
    raw_asks = [l for l in raw_asks if l.qty > 0]

    imbalance_raw = compute_imbalance(raw_bids, raw_asks)

    filtered_bids = filter_spoofs(raw_bids, size_multiplier=spoof_multiplier)
    filtered_asks = filter_spoofs(raw_asks, size_multiplier=spoof_multiplier)
    imbalance = compute_imbalance(filtered_bids, filtered_asks)

    if raw_bids and raw_asks:
        mid_price = (raw_bids[0].price + raw_asks[0].price) / Decimal("2")
        spread = raw_asks[0].price - raw_bids[0].price
    else:
        mid_price = Decimal("0")
        spread = Decimal("0")

    snapshot = OBSnapshot(
        ts=ts,
        bids=tuple(raw_bids),
        asks=tuple(raw_asks),
        imbalance_raw=imbalance_raw,
        imbalance=imbalance,
        mid_price=mid_price,
        spread=spread,
    )

    _put_nowait(ob_queue, snapshot, "ob_queue")


def _handle_agg_trade(data: dict, trade_queue: asyncio.Queue) -> None:
    ts_ms = data.get("T") or data.get("E")
    ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc) if ts_ms else utc_now()

    trade = Trade(
        ts=ts,
        price=Decimal(str(data["p"])),
        qty=Decimal(str(data["q"])),
        is_buyer_maker=bool(data["m"]),  # True = sell aggressor
    )
    _put_nowait(trade_queue, trade, "trade_queue")


def _put_nowait(queue: asyncio.Queue, item, name: str) -> None:
    try:
        queue.put_nowait(item)
    except asyncio.QueueFull:
        logger.warning("pipeline_orderbook.queue_full", queue=name)
        # drop oldest, insert newest (maintain freshness)
        try:
            queue.get_nowait()
            queue.put_nowait(item)
        except asyncio.QueueEmpty:
            pass


async def _alert(notify_queue: asyncio.Queue, message: str) -> None:
    event = NotifyEvent(
        event_type=NotifyEventType.ERROR,
        ts=utc_now(),
        message=f"[ORDERBOOK] {message}",
    )
    try:
        notify_queue.put_nowait(event)
    except asyncio.QueueFull:
        pass
