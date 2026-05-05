"""5-minute bar close scheduler. Sleeps until the next bar close + small buffer,
then triggers a tick callback."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Callable

from bot.core.enums import Interval
from bot.core.logging import get_logger

log = get_logger(__name__)


_INTERVAL_SECS = {
    Interval.M1: 60,
    Interval.M5: 300,
    Interval.M15: 900,
    Interval.M60: 3600,
    Interval.M240: 14400,
    Interval.D1: 86400,
}


def next_bar_close(now: datetime, interval: Interval) -> datetime:
    secs = _INTERVAL_SECS[interval]
    epoch = int(now.timestamp())
    next_epoch = ((epoch // secs) + 1) * secs
    return datetime.fromtimestamp(next_epoch, tz=timezone.utc)


def sleep_until(target: datetime, buffer_sec: float = 5.0) -> None:
    delay = (target - datetime.now(timezone.utc)).total_seconds() + buffer_sec
    if delay > 0:
        time.sleep(delay)


def loop(
    interval: Interval,
    on_tick: Callable[[datetime], None],
    *,
    buffer_sec: float = 5.0,
    max_iterations: int | None = None,
) -> None:
    """Run on_tick once per closed bar, forever (or for max_iterations in tests)."""
    iters = 0
    while True:
        target = next_bar_close(datetime.now(timezone.utc), interval)
        log.info("scheduler_wait", target=target.isoformat())
        sleep_until(target, buffer_sec)
        try:
            on_tick(target)
        except Exception as e:
            log.exception("scheduler_tick_error", err=str(e))
        iters += 1
        if max_iterations is not None and iters >= max_iterations:
            return
