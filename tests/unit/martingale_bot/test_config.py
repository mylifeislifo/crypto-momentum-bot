"""Tests for martingale_bot configuration: Bitget screenshot params + doc/ rules."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from martingale_bot.config import (
    CONFIG_AGGRESSIVE,
    MAX_LEVERAGE,
    PARAMS_AGGRESSIVE,
    BacktestConfig,
    MartingaleParams,
    max_cycle_cost,
)


class TestScreenshotParameters:
    """Defaults must equal the Bitget BGB/USDT Aggressive card verbatim."""

    def test_price_drop_step_is_1pct(self):
        assert MartingaleParams().price_drop_step == Decimal("0.01")

    def test_tp_target_is_1pct(self):
        assert MartingaleParams().tp_target == Decimal("0.01")

    def test_max_safety_orders_is_5(self):
        assert MartingaleParams().max_safety_orders == 5

    def test_volume_scale_is_2_5(self):
        assert MartingaleParams().volume_scale == Decimal("2.5")

    def test_step_scale_is_1(self):
        assert MartingaleParams().step_scale == Decimal("1.0")

    def test_immediate_trigger_default(self):
        assert MartingaleParams().immediate_trigger is True

    def test_symbol_is_bgbusdt(self):
        assert BacktestConfig().symbol == "BGBUSDT"

    def test_hard_stop_disabled_by_default(self):
        # vanilla Bitget martingale has NO stop loss
        assert MartingaleParams().hard_stop_pct == Decimal("0")


class TestDomainRules:
    """doc/ rules that are non-negotiable."""

    def test_max_leverage_constant(self):
        assert MAX_LEVERAGE == Decimal("2.0")  # trading §1.1

    def test_default_leverage_is_spot_1x(self):
        assert BacktestConfig().leverage == Decimal("1.0")

    def test_leverage_above_cap_rejected(self):
        with pytest.raises(ValidationError, match="hard cap"):
            BacktestConfig(leverage=Decimal("2.5"))

    def test_leverage_zero_rejected(self):
        with pytest.raises(ValidationError):
            BacktestConfig(leverage=Decimal("0"))

    def test_params_are_frozen(self):
        with pytest.raises(ValidationError):
            PARAMS_AGGRESSIVE.max_safety_orders = 9  # type: ignore[misc]

    def test_config_is_frozen(self):
        with pytest.raises(ValidationError):
            CONFIG_AGGRESSIVE.leverage = Decimal("2.0")  # type: ignore[misc]


class TestDecimalEnforcement:
    """trading §1.2 — money values are Decimal, never float."""

    def test_decimal_fields_are_decimal_type(self):
        p = MartingaleParams()
        for v in (p.price_drop_step, p.tp_target, p.volume_scale, p.step_scale,
                  p.base_order_size, p.safety_order_size, p.hard_stop_pct):
            assert isinstance(v, Decimal)
        c = BacktestConfig()
        for v in (c.initial_capital, c.leverage, c.taker_fee, c.slippage):
            assert isinstance(v, Decimal)

    def test_float_input_coerced_via_str(self):
        # 0.1 has no exact float repr; Decimal(str(0.1)) stays clean
        p = MartingaleParams(price_drop_step=0.1)
        assert p.price_drop_step == Decimal("0.1")
        assert isinstance(p.price_drop_step, Decimal)

    def test_str_input_coerced(self):
        c = BacktestConfig(initial_capital="25000")
        assert c.initial_capital == Decimal("25000")

    def test_bool_rejected_as_decimal(self):
        with pytest.raises(ValidationError):
            BacktestConfig(leverage=True)


class TestParamValidation:
    def test_volume_scale_below_one_rejected(self):
        with pytest.raises(ValidationError, match="volume_scale"):
            MartingaleParams(volume_scale=Decimal("0.9"))

    def test_step_scale_must_be_positive(self):
        with pytest.raises(ValidationError, match="step_scale"):
            MartingaleParams(step_scale=Decimal("0"))

    def test_tp_target_out_of_range_rejected(self):
        with pytest.raises(ValidationError):
            MartingaleParams(tp_target=Decimal("1.5"))

    def test_negative_price_step_rejected(self):
        with pytest.raises(ValidationError):
            MartingaleParams(price_drop_step=Decimal("-0.01"))

    def test_hard_stop_in_range_accepted(self):
        assert MartingaleParams(hard_stop_pct=Decimal("0.1")).hard_stop_pct == Decimal("0.1")

    def test_hard_stop_at_one_rejected(self):
        with pytest.raises(ValidationError):
            MartingaleParams(hard_stop_pct=Decimal("1.0"))

    def test_deepest_leg_negative_price_rejected(self):
        # step_scale 2.0 over many safety orders blows past 100% cumulative drop
        with pytest.raises(ValidationError, match="cumulative price deviation"):
            MartingaleParams(
                price_drop_step=Decimal("0.10"),
                step_scale=Decimal("2.0"),
                max_safety_orders=6,
            )


class TestMaxCycleCost:
    """The martingale capital trap, made explicit."""

    def test_screenshot_defaults_cost(self):
        # base 100 + 100*(1 + 2.5 + 6.25 + 15.625 + 39.0625) = 100 + 6443.75
        cost = max_cycle_cost(MartingaleParams())
        assert cost == Decimal("6543.75")

    def test_cost_is_decimal(self):
        assert isinstance(max_cycle_cost(MartingaleParams()), Decimal)

    def test_zero_safety_orders_is_base_only(self):
        p = MartingaleParams(max_safety_orders=0)
        assert max_cycle_cost(p) == p.base_order_size
