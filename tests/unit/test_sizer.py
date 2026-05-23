"""Tests for position sizer (Fixed Fractional)."""

from decimal import Decimal

import pytest

from bot.risk.sizer import compute_qty

_ENTRY = Decimal("50000")
_EQUITY = Decimal("10000")


class TestComputeQty:
    def test_basic_sizing(self):
        # risk 1% of 10000 = $100, stop_dist = 50000 * 0.018 = $900
        # qty = 100 / 900 = 0.111... → rounds to 0.111
        stop = _ENTRY * Decimal("0.982")
        qty = compute_qty(_ENTRY, stop, _EQUITY, risk_per_trade=0.01, max_leverage=2)
        assert qty > 0
        stop_dist = _ENTRY - stop
        expected = (_EQUITY * Decimal("0.01")) / stop_dist
        assert abs(qty - expected) < Decimal("0.001")  # within 1 lot

    def test_rounds_down_to_lot_size(self):
        stop = _ENTRY - Decimal("500")
        qty = compute_qty(_ENTRY, stop, _EQUITY, lot_size=Decimal("0.001"))
        # qty must be a multiple of 0.001
        remainder = qty % Decimal("0.001")
        assert remainder == Decimal("0")

    def test_capped_by_leverage(self):
        # very tight stop → qty would be huge without cap
        stop = _ENTRY - Decimal("1")  # $1 stop → qty = 10000 * 0.01 / 1 = 100 BTC
        qty = compute_qty(_ENTRY, stop, _EQUITY, max_leverage=2, lot_size=Decimal("0.001"))
        max_qty = (_EQUITY * 2) / _ENTRY  # = 0.4 BTC
        assert qty <= max_qty

    def test_returns_zero_on_zero_stop_distance(self):
        qty = compute_qty(_ENTRY, _ENTRY, _EQUITY)  # stop == entry
        assert qty == Decimal("0")

    def test_returns_zero_on_zero_equity(self):
        stop = _ENTRY * Decimal("0.982")
        qty = compute_qty(_ENTRY, stop, Decimal("0"))
        assert qty == Decimal("0")

    def test_returns_zero_on_negative_equity(self):
        stop = _ENTRY * Decimal("0.982")
        qty = compute_qty(_ENTRY, stop, Decimal("-100"))
        assert qty == Decimal("0")

    def test_returns_zero_below_min_notional(self):
        # tiny equity → qty * entry < min_notional
        stop = _ENTRY * Decimal("0.982")
        qty = compute_qty(_ENTRY, stop, Decimal("10"), min_notional=Decimal("100"))
        assert qty == Decimal("0")

    def test_short_stop_above_entry(self):
        # For short: stop is above entry
        stop = _ENTRY * Decimal("1.0075")  # +0.75% above
        qty = compute_qty(_ENTRY, stop, _EQUITY)
        assert qty > 0

    def test_result_is_always_positive(self):
        for sl_pct in [0.005, 0.010, 0.015, 0.020]:
            stop = _ENTRY * (Decimal("1") - Decimal(str(sl_pct)))
            qty = compute_qty(_ENTRY, stop, _EQUITY)
            assert qty >= 0

    @pytest.mark.parametrize("equity", [
        Decimal("1000"), Decimal("5000"), Decimal("10000"), Decimal("50000"),
    ])
    def test_scales_with_equity(self, equity: Decimal):
        stop = _ENTRY * Decimal("0.982")
        qty1 = compute_qty(_ENTRY, stop, equity, max_leverage=2)
        qty2 = compute_qty(_ENTRY, stop, equity * 2, max_leverage=2)
        # doubling equity should roughly double qty (modulo leverage cap and rounding)
        assert qty2 >= qty1
