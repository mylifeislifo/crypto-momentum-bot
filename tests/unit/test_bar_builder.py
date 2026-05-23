import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from bot.core.enums import Interval
from bot.core.types import Trade
from bot.data.bar_builder import BarBuilder


def make_trade(ts_epoch: float, price: float, qty: float, is_buyer_maker: bool) -> Trade:
    return Trade(
        ts=datetime.fromtimestamp(ts_epoch, tz=timezone.utc),
        price=Decimal(str(price)),
        qty=Decimal(str(qty)),
        is_buyer_maker=is_buyer_maker,
    )


class TestBarBuilder:
    def setup_method(self):
        self.builder = BarBuilder(intervals=[Interval.M5])

    def test_no_bar_within_same_interval(self):
        # All ticks in the same 5m window → no bar yet
        base = 1_700_000_000.0  # epoch that floors to a 5m boundary
        base = (base // 300) * 300  # align to 5m

        for i in range(5):
            trade = make_trade(base + i * 10, 50000.0, 0.1, False)
            self.builder._ingest(trade)

        bars = self.builder._flush_if_ready()
        assert bars == []

    def test_bar_emitted_on_new_interval(self):
        base = (1_700_000_000.0 // 300) * 300  # aligned 5m boundary

        # ticks in first 5m window
        for i in range(3):
            self.builder._ingest(make_trade(base + i * 60, 50000.0, 1.0, False))

        # one tick in next 5m window → triggers flush of first bar
        self.builder._ingest(make_trade(base + 300, 50100.0, 0.5, True))

        bars = self.builder._flush_if_ready()
        assert len(bars) == 1
        bar = bars[0]
        assert bar.interval == Interval.M5
        assert bar.open == Decimal("50000.0")
        assert bar.close == Decimal("50000.0")
        assert bar.volume == Decimal("3.0")
        assert bar.trade_count == 3

    def test_cvd_delta_calculation(self):
        base = (1_700_000_000.0 // 300) * 300

        # 2 buy aggressors (is_buyer_maker=False), 1 sell aggressor
        self.builder._ingest(make_trade(base + 0, 50000, 1.0, False))  # buy aggressor
        self.builder._ingest(make_trade(base + 10, 50000, 2.0, False))  # buy aggressor
        self.builder._ingest(make_trade(base + 20, 50000, 1.0, True))   # sell aggressor

        # trigger flush
        self.builder._ingest(make_trade(base + 300, 50100, 0.1, False))

        bars = self.builder._flush_if_ready()
        assert len(bars) == 1
        bar = bars[0]
        # buy_vol=3.0, sell_vol=1.0 → delta=2.0
        assert bar.cvd_delta == pytest.approx(2.0)
        assert float(bar.buy_volume) == pytest.approx(3.0)
        assert float(bar.sell_volume) == pytest.approx(1.0)

    def test_cvd_cumulative_accumulates(self):
        base = (1_700_000_000.0 // 300) * 300

        # bar 1: delta = +1.0
        self.builder._ingest(make_trade(base + 0, 50000, 2.0, False))   # buy
        self.builder._ingest(make_trade(base + 10, 50000, 1.0, True))   # sell
        self.builder._ingest(make_trade(base + 300, 50000, 3.0, False)) # buy (bar 2 start)
        bars = self.builder._flush_if_ready()
        assert bars[0].cvd_cumulative == pytest.approx(1.0)

        # bar 2: delta = +2.0
        self.builder._ingest(make_trade(base + 310, 50000, 1.0, True))  # sell
        self.builder._ingest(make_trade(base + 600, 50000, 0.1, False)) # bar 3 start
        bars = self.builder._flush_if_ready()
        # bar 2: buy=3.0, sell=1.0 → delta=2.0, cumulative=3.0
        assert bars[0].cvd_cumulative == pytest.approx(3.0)


@pytest.mark.asyncio
async def test_bar_builder_run_emits_bars():
    trade_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
    bar_queue: asyncio.Queue = asyncio.Queue(maxsize=100)

    builder = BarBuilder(intervals=[Interval.M5])
    base = (1_700_000_000.0 // 300) * 300

    # fill trade_queue
    for i in range(3):
        await trade_queue.put(make_trade(base + i * 60, 50000.0, 1.0, False))
    await trade_queue.put(make_trade(base + 300, 50100.0, 0.5, True))  # triggers bar

    task = asyncio.create_task(builder.run(trade_queue, bar_queue))
    await asyncio.sleep(0.1)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert not bar_queue.empty()
    bar = bar_queue.get_nowait()
    assert bar.interval == Interval.M5
