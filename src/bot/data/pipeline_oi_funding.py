"""Pipeline B: Binance Futures REST — Open Interest & Funding Rate.

Polls every `poll_sec` seconds (default 60).
Computes OI delta vs previous reading.
Outputs OIFunding objects to oi_queue.
"""

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal

import aiohttp
from tenacity import retry, stop_after_attempt, wait_exponential

from ..core.clock import utc_now
from ..core.types import NotifyEvent, OIFunding
from ..core.enums import NotifyEventType

logger = logging.getLogger(__name__)

_FAPI_BASE = "https://fapi.binance.com"
_OI_URL = f"{_FAPI_BASE}/fapi/v1/openInterest"
_FUNDING_URL = f"{_FAPI_BASE}/fapi/v1/fundingRate"
_PREMIUM_URL = f"{_FAPI_BASE}/fapi/v1/premiumIndex"  # includes next funding time


async def run(
    oi_queue: asyncio.Queue,
    notify_queue: asyncio.Queue,
    symbol: str = "BTCUSDT",
    poll_sec: int = 60,
) -> None:
    prev_oi: Decimal | None = None

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                reading = await _fetch(session, symbol, prev_oi)
                prev_oi = reading.open_interest

                try:
                    oi_queue.put_nowait(reading)
                except asyncio.QueueFull:
                    logger.warning("oi_funding_pipeline.queue_full")
                    oi_queue.get_nowait()
                    oi_queue.put_nowait(reading)

                logger.debug(
                    "oi_funding.tick",
                    oi=str(reading.open_interest),
                    oi_delta_pct=round(reading.oi_delta_pct * 100, 4),
                    funding=reading.funding_rate,
                )

            except asyncio.CancelledError:
                logger.info("oi_funding_pipeline.cancelled")
                return
            except Exception as exc:
                logger.error("oi_funding_pipeline.error", error=str(exc))
                await _alert(notify_queue, f"OI/Funding fetch error: {exc}")

            await asyncio.sleep(poll_sec)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, max=4))
async def _fetch(
    session: aiohttp.ClientSession,
    symbol: str,
    prev_oi: Decimal | None,
) -> OIFunding:
    async with session.get(_OI_URL, params={"symbol": symbol}) as r:
        r.raise_for_status()
        oi_data = await r.json()

    async with session.get(_PREMIUM_URL, params={"symbol": symbol}) as r:
        r.raise_for_status()
        premium_data = await r.json()

    open_interest = Decimal(str(oi_data["openInterest"]))
    oi_delta_pct = 0.0
    if prev_oi and prev_oi > 0:
        oi_delta_pct = float((open_interest - prev_oi) / prev_oi)

    funding_rate = float(premium_data.get("lastFundingRate", 0))
    next_funding_ts_ms = int(premium_data.get("nextFundingTime", 0))
    next_funding_ts = datetime.fromtimestamp(next_funding_ts_ms / 1000, tz=timezone.utc)

    return OIFunding(
        ts=utc_now(),
        open_interest=open_interest,
        oi_delta_pct=oi_delta_pct,
        funding_rate=funding_rate,
        next_funding_ts=next_funding_ts,
    )


async def _alert(notify_queue: asyncio.Queue, message: str) -> None:
    event = NotifyEvent(
        event_type=NotifyEventType.ERROR,
        ts=utc_now(),
        message=f"[OI/FUNDING] {message}",
    )
    try:
        notify_queue.put_nowait(event)
    except asyncio.QueueFull:
        pass
