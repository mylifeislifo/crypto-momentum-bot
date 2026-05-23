"""Tests for RiskGuard.

Covers:
- Circuit breaker trips at daily_loss_limit
- Blocked trades when CB is active
- KST daily reset re-enables trading
- Pre-trade check sequence (5 gates in order)
- Short eligibility: daily limit + rolling 20-trade bias window
- open_position_count tracking
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from bot.config.schema import AppConfig, ExchangeCfg, RiskCfg
from bot.core.enums import Side
from bot.core.types import OBLevel, OBSnapshot, OIFunding, SentimentReading, Signal
from bot.core.enums import SentimentLabel, Interval
from bot.risk.guard import RiskGuard

_TS = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
_EQUITY = Decimal("10000")


def _guard(
    daily_loss_limit: float = -0.03,
    max_positions: int = 3,
    short_max_daily: int = 1,
    long_bias_min: float = 0.80,
    long_bias_window: int = 20,
) -> RiskGuard:
    risk = RiskCfg(
        daily_loss_limit=daily_loss_limit,
        max_positions=max_positions,
        short_max_daily=short_max_daily,
        long_bias_min=long_bias_min,
        long_bias_window=long_bias_window,
    )
    exchange = ExchangeCfg()
    return RiskGuard(risk, exchange)


def _signal(side: Side = Side.LONG) -> Signal:
    entry = Decimal("50000")
    stop = entry * Decimal("0.982") if side == Side.LONG else entry * Decimal("1.0075")
    return Signal(
        ts=_TS,
        side=side,
        entry_price_est=entry,
        stop_price=stop,
        confidence=0.8,
        macro_gate=True,
        micro_gate=True,
        cvd_gate=True,
        fear_greed=20,
        funding_rate=-0.0002,
        oi_delta_pct=0.005,
        imbalance=0.45,
        cvd_delta_sum=10.0,
    )


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

class TestCircuitBreaker:
    def test_trips_at_loss_limit(self):
        guard = _guard(daily_loss_limit=-0.03)
        guard.update_equity(_EQUITY)                          # set baseline

        # equity drops 3.1% below baseline → should trip
        cb = guard.update_equity(_EQUITY * Decimal("0.969"))
        assert cb is not None
        assert cb.daily_pnl_pct <= -0.03

    def test_does_not_trip_below_limit(self):
        guard = _guard(daily_loss_limit=-0.03)
        guard.update_equity(_EQUITY)
        cb = guard.update_equity(_EQUITY * Decimal("0.975"))  # -2.5%, not yet
        assert cb is None

    def test_cb_blocks_pre_trade_check(self):
        guard = _guard(daily_loss_limit=-0.03)
        guard.update_equity(_EQUITY)
        guard.update_equity(_EQUITY * Decimal("0.969"))       # trip CB

        allowed, reason = guard.pre_trade_check(_signal(), _EQUITY)
        assert not allowed
        assert "Circuit breaker" in reason

    def test_cb_sets_trading_allowed_false(self):
        guard = _guard()
        guard.update_equity(_EQUITY)
        guard.update_equity(_EQUITY * Decimal("0.969"))
        assert not guard.is_trading_allowed

    def test_cb_not_double_tripped(self):
        guard = _guard()
        guard.update_equity(_EQUITY)
        cb1 = guard.update_equity(_EQUITY * Decimal("0.969"))
        cb2 = guard.update_equity(_EQUITY * Decimal("0.950"))  # even worse
        assert cb1 is not None
        assert cb2 is None  # already tripped, no second event

    def test_cb_reset_re_enables_trading(self):
        guard = _guard()
        guard.update_equity(_EQUITY)
        guard.update_equity(_EQUITY * Decimal("0.969"))
        assert not guard.is_trading_allowed

        # simulate past the KST 09:00 reset
        past_reset = datetime.now(timezone.utc) - timedelta(seconds=1)
        guard._state.reset_at_utc = past_reset

        # calling anything that runs _maybe_reset() should reset
        guard.update_equity(_EQUITY)  # new day, fresh baseline
        assert guard.is_trading_allowed


# ---------------------------------------------------------------------------
# Pre-trade check gate order
# ---------------------------------------------------------------------------

class TestPreTradeCheck:
    def test_allows_normal_long(self):
        guard = _guard()
        guard.update_equity(_EQUITY)
        allowed, reason = guard.pre_trade_check(_signal(Side.LONG), _EQUITY)
        assert allowed
        assert reason == ""

    def test_blocks_at_max_positions(self):
        guard = _guard(max_positions=2)
        guard.update_equity(_EQUITY)
        guard.on_trade_opened(Side.LONG)
        guard.on_trade_opened(Side.LONG)

        allowed, reason = guard.pre_trade_check(_signal(), _EQUITY)
        assert not allowed
        assert "Max positions" in reason

    def test_blocks_on_zero_equity(self):
        guard = _guard()
        guard.update_equity(_EQUITY)
        allowed, reason = guard.pre_trade_check(_signal(), Decimal("0"))
        assert not allowed
        assert "Equity" in reason

    def test_open_position_count_increments(self):
        guard = _guard()
        assert guard.open_position_count == 0
        guard.on_trade_opened(Side.LONG)
        assert guard.open_position_count == 1
        guard.on_trade_opened(Side.LONG)
        assert guard.open_position_count == 2

    def test_open_position_count_decrements_on_close(self):
        guard = _guard()
        guard.on_trade_opened(Side.LONG)
        guard.on_trade_closed(Side.LONG)
        assert guard.open_position_count == 0

    def test_open_position_count_never_negative(self):
        guard = _guard()
        guard.on_trade_closed(Side.LONG)  # close without open
        assert guard.open_position_count == 0


# ---------------------------------------------------------------------------
# Short eligibility
# ---------------------------------------------------------------------------

class TestShortEligibility:
    def test_allows_first_short(self):
        guard = _guard(short_max_daily=1)
        guard.update_equity(_EQUITY)
        allowed, reason = guard.pre_trade_check(_signal(Side.SHORT), _EQUITY)
        assert allowed
        assert reason == ""

    def test_blocks_second_short_same_day(self):
        guard = _guard(short_max_daily=1)
        guard.update_equity(_EQUITY)
        guard.on_trade_opened(Side.SHORT)  # first short opened

        allowed, reason = guard.pre_trade_check(_signal(Side.SHORT), _EQUITY)
        assert not allowed
        assert "Max shorts today" in reason

    def test_long_not_affected_by_short_daily_limit(self):
        guard = _guard(short_max_daily=1)
        guard.update_equity(_EQUITY)
        guard.on_trade_opened(Side.SHORT)  # max shorts reached

        allowed, _ = guard.pre_trade_check(_signal(Side.LONG), _EQUITY)
        assert allowed

    def test_short_blocked_by_bias_window(self):
        # Fill 20-trade window with 4 shorts (20%) → exactly at max, should block
        guard = _guard(long_bias_min=0.80, long_bias_window=20, short_max_daily=99)
        guard.update_equity(_EQUITY)

        # 16 longs + 4 shorts in history = 20% shorts → at the limit → blocked
        for _ in range(16):
            guard.on_trade_opened(Side.LONG)
            guard.on_trade_closed(Side.LONG)
        for _ in range(4):
            guard.on_trade_opened(Side.SHORT)
            guard.on_trade_closed(Side.SHORT)

        allowed, reason = guard.pre_trade_check(_signal(Side.SHORT), _EQUITY)
        assert not allowed
        assert "Long bias" in reason

    def test_short_allowed_when_bias_window_not_full(self):
        # Fewer than `long_bias_window` trades → bias check skipped
        guard = _guard(long_bias_min=0.80, long_bias_window=20, short_max_daily=99)
        guard.update_equity(_EQUITY)
        # Only 5 longs and 1 short → window not full → bias check bypassed
        for _ in range(5):
            guard.on_trade_opened(Side.LONG)
            guard.on_trade_closed(Side.LONG)

        allowed, _ = guard.pre_trade_check(_signal(Side.SHORT), _EQUITY)
        assert allowed

    def test_shorts_today_resets_on_daily_reset(self):
        guard = _guard(short_max_daily=1)
        guard.update_equity(_EQUITY)
        guard.on_trade_opened(Side.SHORT)
        assert guard._state.shorts_today == 1

        # force KST reset
        guard._state.reset_at_utc = datetime.now(timezone.utc) - timedelta(seconds=1)
        guard._maybe_reset()

        assert guard._state.shorts_today == 0


# ---------------------------------------------------------------------------
# Daily PnL tracking
# ---------------------------------------------------------------------------

class TestDailyPnl:
    def test_pnl_computed_correctly(self):
        guard = _guard()
        guard.update_equity(Decimal("10000"))
        guard.update_equity(Decimal("10100"))
        assert abs(guard.daily_pnl_pct - 0.01) < 1e-6

    def test_pnl_resets_on_daily_reset(self):
        guard = _guard()
        guard.update_equity(Decimal("10000"))
        guard.update_equity(Decimal("10200"))
        assert guard.daily_pnl_pct > 0

        guard._state.reset_at_utc = datetime.now(timezone.utc) - timedelta(seconds=1)
        guard.update_equity(Decimal("10000"))
        assert guard.daily_pnl_pct == 0.0  # fresh baseline
