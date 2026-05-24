"""Bar builder: aggregates raw Trade ticks into 5m and 15m OHLCV+CVD bars.

Reads from trade_queue. On each bar boundary, computes:
  - OHLCV
  - CVD delta (buy_vol - sell_vol for the bar)
  - CVD cumulative (running total since start)
  - VWAP

Uses Polars for fast vectorized aggregation over the accumulated tick buffer.
Emits Bar objects to bar_queue for each completed interval.
"""

import asyncio
import logging
from decimal import Decimal

import polars as pl

from ..core.clock import floor_to_interval, utc_now
from ..core.enums import Interval
from ..core.types import Bar, Trade

logger = logging.getLogger(__name__)

_INTERVAL_SECS: dict[Interval, int] = {
    Interval.M5: 300,
    Interval.M15: 900,
}


class BarBuilder:
    def __init__(self, intervals: list[Interval] | None = None) -> None:
        self._intervals = intervals or [Interval.M5, Interval.M15]
        self._ticks: list[dict] = []
        self._cvd_cumulative: float = 0.0
        # track the last completed bar open time per interval
        self._last_bar_ts: dict[Interval, float] = {}

    async def run(self, trade_queue: asyncio.Queue, bar_queue: asyncio.Queue) -> None:
        while True:
            try:
                trade: Trade = await trade_queue.get()
                self._ingest(trade)
                bars = self._flush_if_ready()
                for bar in bars:
                    try:
                        bar_queue.put_nowait(bar)
                    except asyncio.QueueFull:
                        logger.warning("bar_builder.bar_queue_full")
                        bar_queue.get_nowait()
                        bar_queue.put_nowait(bar)
            except asyncio.CancelledError:
                logger.info("bar_builder.cancelled")
                return

    def _ingest(self, trade: Trade) -> None:
        self._ticks.append({
            "ts": trade.ts.timestamp(),
            "price": float(trade.price),
            "qty": float(trade.qty),
            "is_buyer_maker": trade.is_buyer_maker,
        })
        # keep buffer bounded: drop ticks older than largest interval + 1 extra bar
        max_window = max(_INTERVAL_SECS.values()) * 2
        cutoff = trade.ts.timestamp() - max_window
        self._ticks = [t for t in self._ticks if t["ts"] >= cutoff]

    def _flush_if_ready(self) -> list[Bar]:
        if not self._ticks:
            return []

        now_ts = self._ticks[-1]["ts"]
        bars: list[Bar] = []

        for interval in self._intervals:
            sec = _INTERVAL_SECS[interval]
            current_bar_ts = (now_ts // sec) * sec
            last = self._last_bar_ts.get(interval)

            if last is None:
                # first call: anchor to the oldest tick in the buffer so bars
                # spanning the first period are emitted correctly
                first_ts = self._ticks[0]["ts"] if self._ticks else now_ts
                last = (first_ts // sec) * sec
                self._last_bar_ts[interval] = last

            if current_bar_ts > last:
                # a new bar period has started: build bar for [last, current_bar_ts)
                bar = self._build_bar(interval, sec, last, current_bar_ts)
                if bar is not None:
                    bars.append(bar)
                self._last_bar_ts[interval] = current_bar_ts

        return bars

    def _build_bar(
        self,
        interval: Interval,
        sec: int,
        from_ts: float,
        to_ts: float,
    ) -> Bar | None:
        window = [t for t in self._ticks if from_ts <= t["ts"] < to_ts]
        if not window:
            return None

        df = pl.DataFrame(window)

        open_price = df["price"].first()
        high_price = df["price"].max()
        low_price = df["price"].min()
        close_price = df["price"].last()
        total_volume = df["qty"].sum()
        trade_count = len(df)

        buy_mask = ~df["is_buyer_maker"]  # buyer_maker=False means buy aggressor
        buy_vol = df.filter(buy_mask)["qty"].sum() or 0.0
        sell_vol = df.filter(df["is_buyer_maker"])["qty"].sum() or 0.0
        cvd_delta = buy_vol - sell_vol
        self._cvd_cumulative += cvd_delta

        pv = (df["price"] * df["qty"]).sum()
        vwap = pv / total_volume if total_volume else close_price

        from datetime import datetime, timezone
        bar_ts = datetime.fromtimestamp(from_ts, tz=timezone.utc)

        return Bar(
            ts=bar_ts,
            interval=interval,
            open=Decimal(str(open_price)),
            high=Decimal(str(high_price)),
            low=Decimal(str(low_price)),
            close=Decimal(str(close_price)),
            volume=Decimal(str(total_volume)),
            buy_volume=Decimal(str(buy_vol)),
            sell_volume=Decimal(str(sell_vol)),
            cvd_delta=cvd_delta,
            cvd_cumulative=self._cvd_cumulative,
            vwap=Decimal(str(vwap)),
            trade_count=trade_count,
        )
