"""Order manager — the only module that touches live orders.

Lifecycle per signal:
  1. pre_trade_check (guard)       → reject if any gate fails
  2. compute_qty (sizer)           → reject if zero
  3. place MARKET entry            → retry up to execution.retry_max
  4. place STOP_MARKET SL          → must succeed within sl_place_timeout_sec
     on failure: emergency market-close + CRITICAL alert
  5. register with trail manager
  6. register position in paper gateway (paper mode only)
  7. guard.on_trade_opened()
  8. emit ENTRY notification

On each new 5m bar (from trail_bar_queue):
  → trail_manager.on_new_bar() → amend server-side SL if stop moved
  → trail_manager.due_time_exits() → market-close unproven positions (time stop)

On each OB snapshot (paper mode):
  → check if mid_price crossed any SL → close + EXIT notification

Circuit breaker:
  → equity poller (every 60s) calls guard.update_equity()
  → if CB trips: close_all_positions + CIRCUIT_BREAKER notification
"""

import asyncio
from decimal import Decimal
from typing import Optional

import structlog

from ..config.schema import AppConfig
from ..core.clock import utc_now
from ..core.enums import NotifyEventType, OrderSide, OrderType, PositionSide, Side
from ..core.types import CircuitBreakerState, ForcedExit, NotifyEvent, Signal, TrailUpdate
from ..risk.guard import RiskGuard
from ..risk.sizer import compute_qty
from ..risk.trail import TrailingStopManager
from .gateway_base import FuturesGateway
from .paper_futures import PaperFuturesGateway

logger = structlog.get_logger(__name__)

_EQUITY_POLL_SEC = 60.0
_OB_POLL_SEC = 0.10


class OrderManager:
    def __init__(
        self,
        gateway: FuturesGateway,
        guard: RiskGuard,
        trail: TrailingStopManager,
        notify_queue: asyncio.Queue,
        config: AppConfig,
    ) -> None:
        self._gw = gateway
        self._guard = guard
        self._trail = trail
        self._notify_queue = notify_queue
        self._cfg = config
        self._last_equity_check = 0.0

    async def run(
        self,
        signal_queue: asyncio.Queue,
        trail_bar_queue: asyncio.Queue,
        ob_snapshot_queue: asyncio.Queue,
    ) -> None:
        logger.info("order_manager.started", mode=self._cfg.mode.value)

        while True:
            try:
                await asyncio.sleep(_OB_POLL_SEC)

                # --- update paper price + check stops ---
                if isinstance(self._gw, PaperFuturesGateway):
                    latest_mid = None
                    while not ob_snapshot_queue.empty():
                        try:
                            snap = ob_snapshot_queue.get_nowait()
                            latest_mid = snap.mid_price
                        except asyncio.QueueEmpty:
                            break
                    if latest_mid is not None:
                        self._gw.update_price(latest_mid)
                        await self._check_paper_stops(latest_mid)

                # --- equity poll + circuit breaker ---
                now = asyncio.get_event_loop().time()
                if now - self._last_equity_check >= _EQUITY_POLL_SEC:
                    self._last_equity_check = now
                    await self._poll_equity()

                # --- consume signals ---
                while not signal_queue.empty():
                    try:
                        signal: Signal = signal_queue.get_nowait()
                        await self._handle_entry(signal)
                    except asyncio.QueueEmpty:
                        break

                # --- trail bar updates ---
                while not trail_bar_queue.empty():
                    try:
                        bar = trail_bar_queue.get_nowait()
                        updates = self._trail.on_new_bar(bar)
                        for update in updates:
                            await self._amend_sl(update)
                        # time stop: cut unproven positions / hard-cap (winner-asymmetry)
                        for forced_exit in self._trail.due_time_exits():
                            await self._force_close(forced_exit)
                    except asyncio.QueueEmpty:
                        break

            except asyncio.CancelledError:
                logger.info("order_manager.cancelled")
                return
            except Exception as exc:
                logger.error("order_manager.unhandled_error", error=str(exc), exc_info=True)

    # ------------------------------------------------------------------
    # Entry flow
    # ------------------------------------------------------------------

    async def _handle_entry(self, signal: Signal) -> None:
        sym = self._cfg.exchange.symbol

        # 1. Pre-trade check
        try:
            equity = await self._gw.get_balance()
        except Exception as exc:
            logger.error("order_manager.balance_fetch_failed", error=str(exc))
            return

        allowed, reason = self._guard.pre_trade_check(signal, equity)
        if not allowed:
            logger.info("order_manager.signal_rejected", reason=reason, side=signal.side.value)
            return

        # 2. Size
        qty = compute_qty(
            entry_price=signal.entry_price_est,
            stop_price=signal.stop_price,
            equity=equity,
            risk_per_trade=self._cfg.risk.risk_per_trade,
            max_leverage=self._cfg.exchange.max_leverage,
        )
        if qty == Decimal("0"):
            logger.warning("order_manager.zero_qty_dropped", equity=str(equity))
            return

        # 3. Determine order sides
        if signal.side == Side.LONG:
            entry_side, pos_side, sl_side = OrderSide.BUY, PositionSide.LONG, OrderSide.SELL
        else:
            entry_side, pos_side, sl_side = OrderSide.SELL, PositionSide.SHORT, OrderSide.BUY

        # 4. Place MARKET entry
        try:
            entry_order = await self._gw.place_order(
                symbol=sym,
                side=entry_side,
                position_side=pos_side,
                order_type=OrderType.MARKET,
                qty=qty,
            )
        except Exception as exc:
            logger.error("order_manager.entry_failed", error=str(exc))
            await self._notify(NotifyEventType.ERROR, f"Entry order failed: {exc}")
            return

        position_id = entry_order.id

        # 5. Place server-side SL (within timeout)
        try:
            sl_order = await asyncio.wait_for(
                self._gw.place_order(
                    symbol=sym,
                    side=sl_side,
                    position_side=pos_side,
                    order_type=OrderType.STOP_MARKET,
                    qty=qty,
                    stop_price=signal.stop_price,
                    reduce_only=True,
                    client_order_id=f"sl_{position_id[:8]}",
                ),
                timeout=self._cfg.execution.sl_place_timeout_sec,
            )
        except Exception as exc:
            logger.critical("order_manager.sl_failed_emergency_close", error=str(exc))
            await self._notify(
                NotifyEventType.ERROR,
                f"CRITICAL: SL placement failed — emergency close all\n{exc}",
            )
            await self._gw.close_all_positions(sym)
            return

        # 6. Register in paper gateway (tracks position state for paper stops)
        if isinstance(self._gw, PaperFuturesGateway):
            self._gw.register_position(
                position_id=position_id,
                symbol=sym,
                side=signal.side,
                position_side=pos_side,
                qty=qty,
                entry_price=signal.entry_price_est,
                sl_price=signal.stop_price,
                sl_order_id=sl_order.id,
            )

        # 7. Register trail (seed ATR from the trail's own 5m bar history; fall back
        #    to 0.5% of entry before any bar has been seen)
        seed_atr = self._trail.current_atr()
        if seed_atr <= 0:
            seed_atr = signal.entry_price_est * Decimal("0.005")
        self._trail.register(
            position_id=position_id,
            side=signal.side,
            sl_order_id=sl_order.id,
            initial_stop=signal.stop_price,
            entry_price=signal.entry_price_est,
            atr=seed_atr,
        )

        # 8. Guard tracking
        self._guard.on_trade_opened(signal.side)

        logger.info(
            "order_manager.position_opened",
            position_id=position_id,
            side=signal.side.value,
            qty=str(qty),
            entry=str(signal.entry_price_est),
            sl=str(signal.stop_price),
        )

        # 9. Notify
        direction = "LONG ↑" if signal.side == Side.LONG else "SHORT ↓"
        await self._notify(
            NotifyEventType.ENTRY,
            f"{direction} {sym}\n"
            f"진입: ${signal.entry_price_est:,.2f}\n"
            f"손절: ${signal.stop_price:,.2f}\n"
            f"수량: {qty} BTC\n"
            f"신뢰도: {signal.confidence:.1%}\n"
            f"펀딩: {signal.funding_rate:.4%} | F&G: {signal.fear_greed}",
        )

    # ------------------------------------------------------------------
    # Trail SL amendment
    # ------------------------------------------------------------------

    async def _amend_sl(self, update: TrailUpdate) -> None:
        sym = self._cfg.exchange.symbol
        if isinstance(self._gw, PaperFuturesGateway):
            self._gw.update_sl_price(update.position_id, update.new_stop_price, update.old_sl_order_id)

        # cancel old SL
        try:
            await self._gw.cancel_order(sym, update.old_sl_order_id)
        except Exception as exc:
            logger.warning("order_manager.cancel_sl_failed", error=str(exc))

        # place new SL
        pos = self._trail._positions.get(update.position_id)
        if not pos:
            return
        sl_side = OrderSide.SELL if pos.side == Side.LONG else OrderSide.BUY
        pos_side = PositionSide.LONG if pos.side == Side.LONG else PositionSide.SHORT

        try:
            # read qty from paper gateway or assume constant
            qty = Decimal("0")
            if isinstance(self._gw, PaperFuturesGateway):
                paper_pos = self._gw._positions.get(update.position_id)
                qty = Decimal(paper_pos.qty) if paper_pos else Decimal("0")

            if qty > 0:
                new_sl = await self._gw.place_order(
                    symbol=sym,
                    side=sl_side,
                    position_side=pos_side,
                    order_type=OrderType.STOP_MARKET,
                    qty=qty,
                    stop_price=update.new_stop_price,
                    reduce_only=True,
                )
                self._trail.update_sl_order_id(update.position_id, new_sl.id)
                logger.info(
                    "order_manager.sl_amended",
                    position_id=update.position_id,
                    old_stop=str(update.old_stop_price),
                    new_stop=str(update.new_stop_price),
                )
        except Exception as exc:
            logger.error("order_manager.amend_sl_failed", error=str(exc))

    # ------------------------------------------------------------------
    # Time stop — market close (winner-asymmetry "cut losers short")
    # ------------------------------------------------------------------

    async def _force_close(self, fx: ForcedExit) -> None:
        """Market-close a position the time stop flagged. Distinct from a price
        stop: this is a non-price, time-based forced exit (reduce-only market)."""
        sym = self._cfg.exchange.symbol

        if isinstance(self._gw, PaperFuturesGateway):
            fill = self._gw.close_position(fx.position_id)
            if fill:                                    # None if already closed (e.g. SL beat us)
                self._trail.on_close(fx.position_id)
                self._guard.on_trade_closed(fill.side)
                logger.info(
                    "order_manager.time_stop_closed",
                    position_id=fx.position_id, reason=fx.reason, bars_held=fx.bars_held,
                )
                await self._notify(
                    NotifyEventType.EXIT,
                    f"⏱ 시간청산 ({fx.reason}) {sym}\n"
                    f"방향: {fill.side.value}\n"
                    f"청산가: ${fill.avg_price:,.2f}\n"
                    f"보유: {fx.bars_held} bars (5m)",
                )
            return

        # live: reduce-only market close + cancel the resting SL
        try:
            gw_pos = await self._gw.get_position(sym)
            if gw_pos is None or gw_pos.qty <= 0:
                self._trail.on_close(fx.position_id)     # already flat
                return
            close_side = OrderSide.SELL if fx.side == Side.LONG else OrderSide.BUY
            pos_side = PositionSide.LONG if fx.side == Side.LONG else PositionSide.SHORT
            await self._gw.place_order(
                symbol=sym, side=close_side, position_side=pos_side,
                order_type=OrderType.MARKET, qty=gw_pos.qty, reduce_only=True,
            )
            state = self._trail._positions.get(fx.position_id)
            if state is not None:
                try:
                    await self._gw.cancel_order(sym, state.sl_order_id)
                except Exception as exc:
                    logger.warning("order_manager.time_stop_cancel_sl_failed", error=str(exc))
            self._trail.on_close(fx.position_id)
            self._guard.on_trade_closed(fx.side)
            logger.info(
                "order_manager.time_stop_closed",
                position_id=fx.position_id, reason=fx.reason, bars_held=fx.bars_held,
            )
            await self._notify(
                NotifyEventType.EXIT,
                f"⏱ 시간청산 ({fx.reason}) {sym}\n방향: {fx.side.value}\n보유: {fx.bars_held} bars (5m)",
            )
        except Exception as exc:
            logger.error(
                "order_manager.force_close_failed", error=str(exc), position_id=fx.position_id
            )

    # ------------------------------------------------------------------
    # Paper stop monitoring
    # ------------------------------------------------------------------

    async def _check_paper_stops(self, mid_price: Decimal) -> None:
        if not isinstance(self._gw, PaperFuturesGateway):
            return
        for pos_id in self._gw.get_triggered_stops(mid_price):
            fill = self._gw.close_position(pos_id)
            if fill:
                self._trail.on_close(pos_id)
                self._guard.on_trade_closed(fill.side)
                await self._notify(
                    NotifyEventType.STOP_HIT,
                    f"손절 청산 {self._cfg.exchange.symbol}\n"
                    f"방향: {fill.side.value}\n"
                    f"청산가: ${fill.avg_price:,.2f}\n"
                    f"수량: {fill.qty} BTC",
                )

    # ------------------------------------------------------------------
    # Circuit breaker & equity polling
    # ------------------------------------------------------------------

    async def _poll_equity(self) -> None:
        try:
            equity = await self._gw.get_balance()
            cb: Optional[CircuitBreakerState] = self._guard.update_equity(equity)
            if cb:
                await self._handle_circuit_breaker(cb)
        except Exception as exc:
            logger.error("order_manager.equity_poll_failed", error=str(exc))

    async def _handle_circuit_breaker(self, cb: CircuitBreakerState) -> None:
        sym = self._cfg.exchange.symbol
        logger.critical("order_manager.circuit_breaker", pnl=cb.daily_pnl_pct, reset_at=cb.reset_at.isoformat())

        try:
            fills = await self._gw.close_all_positions(sym)
            # reconcile the guard's open-position count, else it stays inflated and
            # blocks every future entry with "Max positions" (the count survives the
            # daily reset, which preserves it across days)
            for fill in fills:
                self._guard.on_trade_closed(fill.side)
        except Exception as exc:
            logger.error("order_manager.cb_close_failed", error=str(exc))

        # clear trail state
        for pid in self._trail.active_position_ids():
            self._trail.on_close(pid)

        await self._notify(NotifyEventType.CIRCUIT_BREAKER, cb.message)

    # ------------------------------------------------------------------
    # Notification helper
    # ------------------------------------------------------------------

    async def _notify(self, event_type: NotifyEventType, message: str) -> None:
        event = NotifyEvent(event_type=event_type, ts=utc_now(), message=message)
        try:
            self._notify_queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("order_manager.notify_queue_full")
