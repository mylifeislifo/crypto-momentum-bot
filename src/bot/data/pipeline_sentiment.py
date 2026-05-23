"""Pipeline C: Market Sentiment — Fear & Greed Index + Long/Short Ratio.

Sources:
  - alternative.me  : Crypto Fear & Greed Index (free, no key required)
  - Coinglass       : Global Long/Short ratio (API key required)

Polls every `poll_sec` seconds (default 3600).
Outputs SentimentReading objects to sentiment_queue.
"""

import asyncio
import logging
from typing import Optional

import aiohttp
from tenacity import retry, stop_after_attempt, wait_exponential

from ..core.clock import utc_now
from ..core.enums import NotifyEventType, SentimentLabel
from ..core.types import NotifyEvent, SentimentReading

logger = logging.getLogger(__name__)

_FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=1"
_COINGLASS_LS_URL = "https://open-api.coinglass.com/public/v2/global_long_short_account_ratio"


def _label_from_index(index: int) -> SentimentLabel:
    if index <= 24:
        return SentimentLabel.EXTREME_FEAR
    elif index <= 49:
        return SentimentLabel.FEAR
    elif index <= 54:
        return SentimentLabel.NEUTRAL
    elif index <= 74:
        return SentimentLabel.GREED
    else:
        return SentimentLabel.EXTREME_GREED


async def run(
    sentiment_queue: asyncio.Queue,
    notify_queue: asyncio.Queue,
    coinglass_api_key: str = "",
    poll_sec: int = 3600,
) -> None:
    headers = {"coinglassSecret": coinglass_api_key} if coinglass_api_key else {}

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                reading = await _fetch(session, headers)

                try:
                    sentiment_queue.put_nowait(reading)
                except asyncio.QueueFull:
                    sentiment_queue.get_nowait()
                    sentiment_queue.put_nowait(reading)

                logger.info(
                    "sentiment.tick",
                    fear_greed=reading.fear_greed_index,
                    label=reading.sentiment_label.value,
                    long_ratio=round(reading.long_ratio, 4),
                )

            except asyncio.CancelledError:
                logger.info("sentiment_pipeline.cancelled")
                return
            except Exception as exc:
                logger.error("sentiment_pipeline.error", error=str(exc))
                await _alert(notify_queue, f"Sentiment fetch error: {exc}")

            await asyncio.sleep(poll_sec)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1.0, max=8))
async def _fetch(session: aiohttp.ClientSession, coinglass_headers: dict) -> SentimentReading:
    fear_greed_index = await _fetch_fear_greed(session)
    long_ratio, short_ratio = await _fetch_long_short(session, coinglass_headers)

    return SentimentReading(
        ts=utc_now(),
        fear_greed_index=fear_greed_index,
        sentiment_label=_label_from_index(fear_greed_index),
        long_ratio=long_ratio,
        short_ratio=short_ratio,
    )


async def _fetch_fear_greed(session: aiohttp.ClientSession) -> int:
    async with session.get(_FEAR_GREED_URL) as r:
        r.raise_for_status()
        data = await r.json(content_type=None)  # alternative.me returns text/html
    return int(data["data"][0]["value"])


async def _fetch_long_short(
    session: aiohttp.ClientSession,
    headers: dict,
) -> tuple[float, float]:
    if not headers:
        # No API key: return neutral 50/50 as fallback
        logger.debug("sentiment.no_coinglass_key_using_neutral_ls")
        return 0.50, 0.50

    params = {"symbol": "BTC", "interval": "h1", "limit": 1}
    async with session.get(_COINGLASS_LS_URL, headers=headers, params=params) as r:
        r.raise_for_status()
        data = await r.json()

    row = data["data"][0]
    long_ratio = float(row["longRatio"]) / 100
    short_ratio = float(row["shortRatio"]) / 100
    return long_ratio, short_ratio


async def _alert(notify_queue: asyncio.Queue, message: str) -> None:
    event = NotifyEvent(
        event_type=NotifyEventType.ERROR,
        ts=utc_now(),
        message=f"[SENTIMENT] {message}",
    )
    try:
        notify_queue.put_nowait(event)
    except asyncio.QueueFull:
        pass
