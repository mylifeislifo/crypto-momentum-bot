"""Tests for turtle_bot configuration: video parameters + doc/ rule enforcement."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from turtle_bot.config import (
    CONFIG_M1,
    MAX_LEVERAGE,
    PARAMS_M1,
    BacktestConfig,
    TurtleParams,
)


class TestVideoParameters:
    """Defaults must equal the reference video values verbatim."""

    def test_entry_window_is_20(self):
        assert TurtleParams().entry_window == 20

    def test_exit_window_is_11(self):
        assert TurtleParams().exit_window == 11

    def test_atr_window_is_20(self):
        assert TurtleParams().atr_window == 20

    def test_atr_stop_multiplier_is_2(self):
        assert TurtleParams().atr_stop_multiplier == Decimal("2.0")

    def test_trend_sma_window_is_200(self):
        assert TurtleParams().trend_sma_window == 200

    def test_risk_per_trade_is_2pct(self):
        assert TurtleParams().risk_per_trade == Decimal("0.02")

    def test_symbols_are_btc_and_eth(self):
        assert BacktestConfig().symbols == ("BTCUSDT", "ETHUSDT")

    def test_capital_allocation_is_50_50(self):
        assert BacktestConfig().capital_allocation == Decimal("0.5")

    def test_costs_match_conservative_assumption(self):
        cfg = BacktestConfig()
        assert cfg.taker_fee == Decimal("0.0004")  # 0.04% taker
        assert cfg.slippage == Decimal("0.0005")  # 0.05% slippage
        assert cfg.interval == "1d"


class TestDomainRules:
    """doc/ rules that are non-negotiable."""

    def test_max_leverage_constant(self):
        assert MAX_LEVERAGE == Decimal("2.0")  # trading §1.1

    def test_default_leverage_within_cap(self):
        assert BacktestConfig().leverage <= MAX_LEVERAGE

    def test_leverage_at_cap_allowed(self):
        assert BacktestConfig(leverage=Decimal("2.0")).leverage == Decimal("2.0")

    def test_leverage_above_cap_rejected(self):
        with pytest.raises(ValidationError, match="hard cap"):
            BacktestConfig(leverage=Decimal("2.5"))

    def test_params_are_frozen(self):
        with pytest.raises(ValidationError):
            PARAMS_M1.entry_window = 30  # type: ignore[misc]

    def test_config_is_frozen(self):
        with pytest.raises(ValidationError):
            CONFIG_M1.leverage = Decimal("2.0")  # type: ignore[misc]


class TestDecimalEnforcement:
    """trading §1.2 — money values are Decimal, never float."""

    def test_decimal_fields_are_decimal_type(self):
        params = TurtleParams()
        assert isinstance(params.atr_stop_multiplier, Decimal)
        assert isinstance(params.risk_per_trade, Decimal)
        cfg = BacktestConfig()
        for value in (
            cfg.capital_allocation,
            cfg.initial_capital,
            cfg.leverage,
            cfg.taker_fee,
            cfg.slippage,
        ):
            assert isinstance(value, Decimal)

    def test_float_input_coerced_to_decimal_via_str(self):
        # 0.1 has no exact float repr; Decimal(str(0.1)) must stay clean
        cfg = BacktestConfig(taker_fee=0.1)
        assert cfg.taker_fee == Decimal("0.1")
        assert isinstance(cfg.taker_fee, Decimal)

    def test_str_input_coerced_to_decimal(self):
        cfg = BacktestConfig(initial_capital="25000")
        assert cfg.initial_capital == Decimal("25000")
        assert isinstance(cfg.initial_capital, Decimal)

    def test_int_input_coerced_to_decimal(self):
        params = TurtleParams(risk_per_trade=1)
        assert params.risk_per_trade == Decimal("1")
        assert isinstance(params.risk_per_trade, Decimal)

    def test_bool_rejected_as_decimal(self):
        with pytest.raises(ValidationError):
            BacktestConfig(leverage=True)


class TestModuleSingletons:
    def test_params_m1_is_turtle_params(self):
        assert isinstance(PARAMS_M1, TurtleParams)

    def test_config_m1_is_backtest_config(self):
        assert isinstance(CONFIG_M1, BacktestConfig)
