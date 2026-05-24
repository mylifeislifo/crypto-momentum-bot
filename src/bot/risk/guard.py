"""Pre-trade risk gate and circuit breaker.

RiskGuard is stateful. It must be called in order:
  1. update_equity()    — on every equity snapshot (from position tracker)
  2. pre_trade_check()  — before every order attempt
  3. on_trade_opened()  — after confirmed entry fill
  4. on_trade_closed()  — after confirmed exit fill

Circuit breaker:
  Trips when daily_pnl_pct <= daily_loss_limit (-3%).
  Sets trading_allowed=False. Resets at 09:00 KST next day.
  WebSocket data continues flowing; only ORDER placement is blocked.

Short eligibility:
  Two independent rules (both must pass):
  - Max `short_max_daily` shorts per calendar day (default: 1)
  - Rolling 20-trade bias window: short fraction < (1 - long_bias_min)

All checks are pre-trade (before execution). The circuit breaker also fires
from update_equity() so it trips immediately on unrealized loss, not just
when a trade closes.
"""

import structlog
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional

from ..config.schema import ExchangeCfg, RiskCfg
from ..core.clock import next_9am_kst, utc_now
from ..core.enums import Side
from ..core.types import CircuitBreakerState, Signal

logger = structlog.get_logger(__name__)


@dataclass
class GuardState:
    trading_allowed: bool = True
    start_equity: Decimal = Decimal("0")
    daily_pnl_pct: float = 0.0
    reset_at_utc: datetime = field(default_factory=next_9am_kst)
    # rolling window of last N trade sides (for 80% long bias check)
    trade_history: deque = field(default_factory=lambda: deque(maxlen=20))
    shorts_today: int = 0
    open_position_count: int = 0


class RiskGuard:
    def __init__(self, risk_cfg: RiskCfg, exchange_cfg: ExchangeCfg) -> None:
        self._r = risk_cfg
        self._ex = exchange_cfg
        self._state = GuardState()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def pre_trade_check(self, signal: Signal, current_equity: Decimal) -> tuple[bool, str]:
        """Gate before every order. Returns (allowed, rejection_reason).

        Checks run in strict order: first failure returns immediately.
        Empty rejection_reason means the trade is allowed.
        """
        self._maybe_reset()

        # 1. Circuit breaker
        if not self._state.trading_allowed:
            reset_str = self._state.reset_at_utc.strftime("%Y-%m-%d %H:%M UTC")
            return False, f"Circuit breaker active until {reset_str}"

        # 2. Daily loss limit (defensive — update_equity also checks this)
        if self._state.daily_pnl_pct <= self._r.daily_loss_limit:
            return False, f"Daily loss limit {self._r.daily_loss_limit:.1%} reached ({self._state.daily_pnl_pct:.2%})"

        # 3. Max concurrent positions
        if self._state.open_position_count >= self._r.max_positions:
            return False, f"Max positions ({self._r.max_positions}) reached"

        # 4. Short eligibility
        if signal.side == Side.SHORT:
            ok, reason = self._short_eligible()
            if not ok:
                return False, reason

        # 5. Positive equity guard
        if current_equity <= 0:
            return False, "Equity is zero or negative"

        return True, ""

    def update_equity(self, equity: Decimal) -> Optional[CircuitBreakerState]:
        """Call on every equity snapshot. Returns CircuitBreakerState if CB trips."""
        self._maybe_reset()

        if self._state.start_equity == 0:
            self._state.start_equity = equity
            logger.info("guard.equity_baseline_set", equity=str(equity))
            return None

        self._state.daily_pnl_pct = float(
            (equity - self._state.start_equity) / self._state.start_equity
        )

        if self._state.trading_allowed and self._state.daily_pnl_pct <= self._r.daily_loss_limit:
            return self._trip(equity)

        return None

    def on_trade_opened(self, side: Side) -> None:
        self._state.open_position_count += 1
        self._state.trade_history.append(side)
        if side == Side.SHORT:
            self._state.shorts_today += 1
        logger.info(
            "guard.trade_opened",
            side=side.value,
            open_positions=self._state.open_position_count,
            shorts_today=self._state.shorts_today,
        )

    def on_trade_closed(self, side: Side) -> None:
        self._state.open_position_count = max(0, self._state.open_position_count - 1)
        logger.info(
            "guard.trade_closed",
            side=side.value,
            open_positions=self._state.open_position_count,
        )

    @property
    def is_trading_allowed(self) -> bool:
        return self._state.trading_allowed

    @property
    def daily_pnl_pct(self) -> float:
        return self._state.daily_pnl_pct

    @property
    def open_position_count(self) -> int:
        return self._state.open_position_count

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _short_eligible(self) -> tuple[bool, str]:
        # Rule A: max N shorts per day
        if self._state.shorts_today >= self._r.short_max_daily:
            return False, f"Max shorts today ({self._r.short_max_daily}) reached"

        # Rule B: rolling 20-trade long-bias window
        history = list(self._state.trade_history)
        window = self._r.long_bias_window
        if len(history) >= window:
            short_count = sum(1 for s in history[-window:] if s == Side.SHORT)
            short_ratio = short_count / window
            max_short_ratio = 1.0 - self._r.long_bias_min
            if short_ratio >= max_short_ratio:
                return False, (
                    f"Long bias constraint: {short_ratio:.1%} shorts in last {window} trades "
                    f"(max {max_short_ratio:.1%})"
                )

        return True, ""

    def _trip(self, equity: Decimal) -> CircuitBreakerState:
        self._state.trading_allowed = False
        reset_at = next_9am_kst()
        self._state.reset_at_utc = reset_at

        cb = CircuitBreakerState(
            triggered_at=utc_now(),
            reset_at=reset_at,
            daily_pnl_pct=self._state.daily_pnl_pct,
            message=(
                f"Daily loss limit hit: {self._state.daily_pnl_pct:.2%}. "
                f"Bot suspended until {reset_at.strftime('%Y-%m-%d %H:%M UTC')} (09:00 KST)"
            ),
        )
        logger.critical(
            "guard.circuit_breaker_tripped",
            daily_pnl_pct=self._state.daily_pnl_pct,
            equity=str(equity),
            reset_at=reset_at.isoformat(),
        )
        return cb

    def _maybe_reset(self) -> None:
        """Reset daily state if the KST 09:00 reset time has passed."""
        now = utc_now()
        if now >= self._state.reset_at_utc:
            prev_pnl = self._state.daily_pnl_pct
            # preserve open_position_count (positions carry over between days)
            open_count = self._state.open_position_count
            history = self._state.trade_history

            self._state = GuardState()
            self._state.open_position_count = open_count
            self._state.trade_history = history  # preserve bias window
            self._state.reset_at_utc = next_9am_kst()

            logger.info(
                "guard.daily_reset",
                prev_daily_pnl=prev_pnl,
                next_reset=self._state.reset_at_utc.isoformat(),
            )
