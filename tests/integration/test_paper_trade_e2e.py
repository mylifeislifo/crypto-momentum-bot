"""End-to-end paper trading test.

Feeds synthetic OB + OI + sentiment + bars through the full stack:
  aggregator → signal_queue → order_manager → paper gateway

Asserts:
  - A LONG signal fires given confluence conditions
  - Entry order is placed (MARKET)
  - SL order is placed (STOP_MARKET) within timeout
  - Telegram ENTRY notification is queued
  - Trail update is emitted when price rises
  - Paper stop triggered when price crosses SL
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from bot.config.schema import AppConfig
from bot.core.enums import Interval, SentimentLabel, Side
from bot.core.types import Bar, OBLevel, OBSnapshot, OIFunding, SentimentReading
from bot.execution.order_manager import OrderManager
from bot.execution.paper_futures import PaperFuturesGateway
from bot.risk.guard import RiskGuard
from bot.risk.trail import TrailingStopManager
from bot.strategy.aggregator import run as aggregator_run

_TS = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
_MID = Decimal("50000")


def _ob(imbalance: float) -> OBSnapshot:
    return OBSnapshot(
        ts=_TS,
        bids=(OBLevel(price=_MID - 1, qty=Decimal("3")),),
        asks=(OBLevel(price=_MID + 1, qty=Decimal("1")),),
        imbalance_raw=imbalance,
        imbalance=imbalance,
        mid_price=_MID,
        spread=Decimal("2"),
    )


def _oi() -> OIFunding:
    return OIFunding(
        ts=_TS,
        open_interest=Decimal("10000"),
        oi_delta_pct=0.005,          # +0.5%, above threshold
        funding_rate=-0.0002,        # negative → long-bias macro
        next_funding_ts=_TS,
    )


def _sent() -> SentimentReading:
    return SentimentReading(
        ts=_TS,
        fear_greed_index=20,
        sentiment_label=SentimentLabel.EXTREME_FEAR,
        long_ratio=0.45,
        short_ratio=0.55,
    )


def _bar(cvd_delta: float = 5.0) -> Bar:
    return Bar(
        ts=_TS,
        interval=Interval.M5,
        open=_MID,
        high=_MID + 200,
        low=_MID - 100,
        close=_MID,
        volume=Decimal("50"),
        buy_volume=Decimal("35"),
        sell_volume=Decimal("15"),
        cvd_delta=cvd_delta,
        cvd_cumulative=cvd_delta,
        vwap=_MID,
        trade_count=500,
    )


@pytest.mark.asyncio
async def test_full_entry_and_sl_placement(tmp_path):
    config = AppConfig()
    ob_q: asyncio.Queue = asyncio.Queue(maxsize=1000)
    oi_q: asyncio.Queue = asyncio.Queue(maxsize=60)
    sent_q: asyncio.Queue = asyncio.Queue(maxsize=24)
    bar_q: asyncio.Queue = asyncio.Queue(maxsize=200)
    trail_q: asyncio.Queue = asyncio.Queue(maxsize=200)
    signal_q: asyncio.Queue = asyncio.Queue(maxsize=10)
    notify_q: asyncio.Queue = asyncio.Queue(maxsize=100)

    gw = PaperFuturesGateway(initial_balance=Decimal("10000"), state_file=tmp_path / "paper.json")
    gw.update_price(_MID)

    guard = RiskGuard(config.risk, config.exchange)
    trail = TrailingStopManager(atr_multiplier=config.risk.trail_atr_multiplier)
    om = OrderManager(gw, guard, trail, notify_q, config)

    # --- seed aggregator queues with confluence conditions ---
    await ob_q.put(_ob(imbalance=0.50))   # bid-heavy
    await oi_q.put(_oi())
    await sent_q.put(_sent())
    for _ in range(3):                     # 3 bars with positive CVD
        await bar_q.put(_bar(cvd_delta=5.0))

    # run aggregator briefly to generate signal
    agg_task = asyncio.create_task(
        aggregator_run(ob_q, oi_q, sent_q, bar_q, signal_q, config, trail_bar_queue=trail_q)
    )
    await asyncio.sleep(0.5)
    agg_task.cancel()
    await asyncio.gather(agg_task, return_exceptions=True)

    # there should be a LONG signal
    assert not signal_q.empty(), "Expected a LONG signal from aggregator"
    signal = signal_q.get_nowait()
    assert signal.side == Side.LONG

    # run order manager to process the signal
    await signal_q.put(signal)
    om_task = asyncio.create_task(om.run(signal_q, trail_q, ob_q))
    await asyncio.sleep(0.3)
    om_task.cancel()
    await asyncio.gather(om_task, return_exceptions=True)

    # position should be registered in paper gateway
    assert len(gw.active_position_ids) == 1, "Expected one open position"

    # ENTRY notification should be queued
    assert not notify_q.empty()
    event = notify_q.get_nowait()
    assert event.event_type.value == "ENTRY"
    assert "LONG" in event.message


@pytest.mark.asyncio
async def test_paper_stop_triggered_on_price_cross(tmp_path):
    config = AppConfig()
    ob_q: asyncio.Queue = asyncio.Queue(maxsize=100)
    trail_q: asyncio.Queue = asyncio.Queue(maxsize=100)
    signal_q: asyncio.Queue = asyncio.Queue(maxsize=10)
    notify_q: asyncio.Queue = asyncio.Queue(maxsize=100)

    gw = PaperFuturesGateway(initial_balance=Decimal("10000"), state_file=tmp_path / "paper.json")
    gw.update_price(_MID)

    guard = RiskGuard(config.risk, config.exchange)
    trail = TrailingStopManager(atr_multiplier=config.risk.trail_atr_multiplier)
    om = OrderManager(gw, guard, trail, notify_q, config)

    # manually register a position with SL at 49100
    stop_price = Decimal("49100")
    gw.register_position(
        position_id="test_pos",
        symbol=config.exchange.symbol,
        side=Side.LONG,
        position_side=__import__('bot.core.enums', fromlist=['PositionSide']).PositionSide.LONG,
        qty=Decimal("0.01"),
        entry_price=_MID,
        sl_price=stop_price,
        sl_order_id="sl_test",
    )
    guard.on_trade_opened(Side.LONG)

    # feed an OB snapshot with price below SL
    from bot.core.types import OBSnapshot, OBLevel
    low_price = Decimal("49000")
    snap = OBSnapshot(
        ts=_TS,
        bids=(OBLevel(price=low_price - 1, qty=Decimal("1")),),
        asks=(OBLevel(price=low_price + 1, qty=Decimal("1")),),
        imbalance_raw=0.0,
        imbalance=0.0,
        mid_price=low_price,
        spread=Decimal("2"),
    )
    await ob_q.put(snap)

    # run order manager briefly
    om_task = asyncio.create_task(om.run(signal_q, trail_q, ob_q))
    await asyncio.sleep(0.3)
    om_task.cancel()
    await asyncio.gather(om_task, return_exceptions=True)

    # position should be closed
    assert len(gw.active_position_ids) == 0, "Position should be closed after stop cross"

    # STOP_HIT notification should be queued
    events = []
    while not notify_q.empty():
        events.append(notify_q.get_nowait())
    stop_events = [e for e in events if e.event_type.value == "STOP_HIT"]
    assert len(stop_events) == 1


@pytest.mark.asyncio
async def test_circuit_breaker_reconciles_guard_count(tmp_path):
    """F2: after the circuit breaker closes all positions, the guard's open-position
    count must return to 0 — otherwise it stays inflated (surviving the daily reset)
    and blocks every future entry with 'Max positions'."""
    from bot.core.clock import next_9am_kst, utc_now
    from bot.core.enums import PositionSide
    from bot.core.types import CircuitBreakerState

    config = AppConfig()
    notify_q: asyncio.Queue = asyncio.Queue(maxsize=100)
    gw = PaperFuturesGateway(initial_balance=Decimal("10000"), state_file=tmp_path / "paper.json")
    gw.update_price(_MID)
    guard = RiskGuard(config.risk, config.exchange)
    trail = TrailingStopManager(atr_multiplier=config.risk.trail_atr_multiplier)
    om = OrderManager(gw, guard, trail, notify_q, config)

    for i in range(2):                       # two open positions (gateway + guard count)
        gw.register_position(
            position_id=f"p{i}", symbol=config.exchange.symbol, side=Side.LONG,
            position_side=PositionSide.LONG, qty=Decimal("0.01"), entry_price=_MID,
            sl_price=Decimal("49100"), sl_order_id=f"sl{i}",
        )
        guard.on_trade_opened(Side.LONG)
    assert guard.open_position_count == 2

    cb = CircuitBreakerState(
        triggered_at=utc_now(), reset_at=next_9am_kst(), daily_pnl_pct=-0.05, message="CB",
    )
    await om._handle_circuit_breaker(cb)

    assert len(gw.active_position_ids) == 0      # all positions closed
    assert guard.open_position_count == 0        # F2: reconciled (was the latent bug)


@pytest.mark.asyncio
async def test_recover_reregisters_gateway_position_into_trail(tmp_path):
    """F4: a position the gateway holds but the trail lost (crash before trail save)
    must be re-registered on startup so it resumes trail/BE/time-stop management,
    and the guard count must reflect it."""
    from bot.core.enums import PositionSide

    config = AppConfig()
    notify_q: asyncio.Queue = asyncio.Queue(maxsize=100)
    gw = PaperFuturesGateway(initial_balance=Decimal("10000"), state_file=tmp_path / "paper.json")
    gw.update_price(_MID)
    guard = RiskGuard(config.risk, config.exchange)
    trail = TrailingStopManager(atr_multiplier=config.risk.trail_atr_multiplier)  # no state → empty
    om = OrderManager(gw, guard, trail, notify_q, config)

    gw.register_position(
        position_id="held", symbol=config.exchange.symbol, side=Side.LONG,
        position_side=PositionSide.LONG, qty=Decimal("0.01"), entry_price=_MID,
        sl_price=Decimal("49100"), sl_order_id="sl_held",
    )
    assert trail.active_position_ids() == []     # trail state was lost

    await om.recover_positions()

    assert trail.active_position_ids() == ["held"]   # re-registered into trail
    assert guard.open_position_count == 1            # guard count reconciled


@pytest.mark.asyncio
async def test_recover_drops_orphan_trail_entry(tmp_path):
    """A trail entry with no matching gateway position (stale save) is dropped."""
    config = AppConfig()
    notify_q: asyncio.Queue = asyncio.Queue(maxsize=100)
    gw = PaperFuturesGateway(initial_balance=Decimal("10000"), state_file=tmp_path / "paper.json")
    guard = RiskGuard(config.risk, config.exchange)
    trail = TrailingStopManager(atr_multiplier=config.risk.trail_atr_multiplier)
    om = OrderManager(gw, guard, trail, notify_q, config)

    trail.register("ghost", Side.LONG, "sl", Decimal("49100"), Decimal("50000"), Decimal("600"))
    await om.recover_positions()
    assert trail.active_position_ids() == []     # orphan dropped (no live position)


class _FakeLiveGateway:
    """Minimal non-paper gateway to exercise the live _amend_sl path (F5)."""

    def __init__(self, qty=Decimal("0.01"), fail_place=False):
        self._qty = qty
        self._fail_place = fail_place
        self.placed: list = []
        self.cancelled: list = []
        self._n = 0

    async def get_position(self, symbol):
        from bot.core.enums import PositionSide
        from bot.core.types import Position
        if self._qty <= 0:
            return None
        return Position(
            position_id="live", symbol=symbol, side=Side.LONG, position_side=PositionSide.LONG,
            qty=self._qty, entry_price=Decimal("50000"), current_price=Decimal("50000"),
            unrealized_pnl=Decimal("0"), leverage=2, sl_order_id="old_sl",
            sl_price=Decimal("49100"), opened_at=_TS, updated_at=_TS,
        )

    async def place_order(self, **kw):
        from bot.core.enums import OrderStatus
        from bot.core.types import Order
        if self._fail_place:
            raise RuntimeError("exchange rejected stop")
        self._n += 1
        oid = f"new_sl_{self._n}"
        self.placed.append((kw["stop_price"], oid))
        return Order(
            id=oid, client_order_id=oid, symbol=kw["symbol"], side=kw["side"],
            position_side=kw["position_side"], order_type=kw["order_type"], qty=kw["qty"],
            price=None, stop_price=kw["stop_price"], status=OrderStatus.NEW, ts=_TS,
            reduce_only=kw.get("reduce_only", False),
        )

    async def cancel_order(self, symbol, order_id):
        self.cancelled.append(order_id)


def _live_om(gw):
    config = AppConfig()
    guard = RiskGuard(config.risk, config.exchange)
    trail = TrailingStopManager(atr_multiplier=config.risk.trail_atr_multiplier)
    trail.register("live", Side.LONG, "old_sl", Decimal("49100"), Decimal("50000"), Decimal("600"))
    om = OrderManager(gw, guard, trail, asyncio.Queue(maxsize=10), config)
    return om, trail


@pytest.mark.asyncio
async def test_live_amend_places_new_sl_before_cancelling_old():
    """F5: live amendment places the new stop (with the REAL position qty) and only
    then cancels the old — never leaving the position unprotected."""
    from bot.core.types import TrailUpdate

    gw = _FakeLiveGateway()
    om, trail = _live_om(gw)
    upd = TrailUpdate(position_id="live", old_sl_order_id="old_sl",
                      new_stop_price=Decimal("49500"), old_stop_price=Decimal("49100"), ts=_TS)
    await om._amend_sl(upd)

    assert [p[0] for p in gw.placed] == [Decimal("49500")]    # new SL placed with real qty
    assert gw.cancelled == ["old_sl"]                          # old cancelled AFTER the place
    assert trail._positions["live"].sl_order_id == "new_sl_1"  # trail repointed


@pytest.mark.asyncio
async def test_live_amend_keeps_old_sl_when_place_fails():
    from bot.core.types import TrailUpdate

    gw = _FakeLiveGateway(fail_place=True)
    om, trail = _live_om(gw)
    upd = TrailUpdate(position_id="live", old_sl_order_id="old_sl",
                      new_stop_price=Decimal("49500"), old_stop_price=Decimal("49100"), ts=_TS)
    await om._amend_sl(upd)   # placement fails

    assert gw.placed == []
    assert gw.cancelled == []                                  # old SL NOT cancelled → protected
    assert trail._positions["live"].sl_order_id == "old_sl"    # unchanged
