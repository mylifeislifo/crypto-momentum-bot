"""Order router with retry/backoff. Wraps a gateway's place_order."""
from __future__ import annotations

import time
from decimal import Decimal

from tenacity import retry, stop_after_attempt, wait_exponential

from bot.core.logging import get_logger
from bot.core.types import Order


log = get_logger(__name__)


class OrderRouter:
    def __init__(self, gateway, max_retries: int = 3, backoff_sec: float = 1.0) -> None:
        self.gateway = gateway
        self.max_retries = max_retries
        self.backoff_sec = backoff_sec
        self.consecutive_failures = 0

    def submit(self, order: Order) -> str | None:
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                oid = self.gateway.place_order(order)
                self.consecutive_failures = 0
                return oid
            except Exception as e:  # broad: gateway errors vary
                last_exc = e
                self.consecutive_failures += 1
                log.warning("order_submit_failed", attempt=attempt, err=str(e), symbol=order.symbol)
                if attempt < self.max_retries:
                    time.sleep(self.backoff_sec * (2 ** (attempt - 1)))
        log.error("order_submit_giveup", err=str(last_exc), symbol=order.symbol)
        return None
