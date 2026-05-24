"""Telegram Bot API notifier.

Reads NotifyEvent objects from notify_queue.
Rate-limited to 1 message per second (Telegram API limit).
Non-blocking: if queue grows > 100 items, oldest are dropped.

Message format per event type is fixed for readability on mobile.
"""

import asyncio

import aiohttp
import structlog

from ..core.enums import NotifyEventType
from ..core.types import NotifyEvent

logger = structlog.get_logger(__name__)

_API_BASE = "https://api.telegram.org/bot{token}/sendMessage"
_RATE_LIMIT_SEC = 1.0

_PREFIXES = {
    NotifyEventType.ENTRY: "🟢 진입",
    NotifyEventType.EXIT: "🔵 청산",
    NotifyEventType.STOP_HIT: "🔴 손절",
    NotifyEventType.TRAIL_UPDATE: "📈 트레일",
    NotifyEventType.CIRCUIT_BREAKER: "🚨 서킷브레이커",
    NotifyEventType.ERROR: "⚠️ 에러",
    NotifyEventType.INFO: "ℹ️ 정보",
}


async def run(
    notify_queue: asyncio.Queue,
    bot_token: str,
    chat_id: str,
    rate_limit_sec: float = _RATE_LIMIT_SEC,
) -> None:
    if not bot_token or not chat_id:
        logger.warning("telegram.disabled_no_credentials")
        # drain queue silently so other modules don't block
        while True:
            try:
                await asyncio.sleep(1.0)
                while not notify_queue.empty():
                    notify_queue.get_nowait()
            except asyncio.CancelledError:
                return

    url = _API_BASE.format(token=bot_token)

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                event: NotifyEvent = await notify_queue.get()
                text = _format(event)

                try:
                    await _send(session, url, chat_id, text)
                except Exception as exc:
                    logger.warning("telegram.send_failed", error=str(exc), event=event.event_type.value)

                await asyncio.sleep(rate_limit_sec)

            except asyncio.CancelledError:
                logger.info("telegram.cancelled")
                return
            except Exception as exc:
                logger.error("telegram.unhandled_error", error=str(exc))


async def _send(session: aiohttp.ClientSession, url: str, chat_id: str, text: str) -> None:
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as r:
        if r.status != 200:
            body = await r.text()
            logger.warning("telegram.api_error", status=r.status, body=body[:200])
        else:
            logger.debug("telegram.sent", event_preview=text[:50])


def _format(event: NotifyEvent) -> str:
    prefix = _PREFIXES.get(event.event_type, "📢")
    ts_str = event.ts.strftime("%H:%M:%S UTC")
    return f"<b>{prefix}</b>\n{event.message}\n\n<i>{ts_str}</i>"
