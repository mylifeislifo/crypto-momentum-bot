"""RiskCfg breakeven validation.

The breakeven offset must stay below the trigger, otherwise the breakeven stop
would land at/above the price that armed it (an SL above market → the exchange
rejects the amendment and breakeven silently fails). Guard it at config load.
"""

import pytest

from bot.config.schema import RiskCfg


def test_default_breakeven_is_valid():
    cfg = RiskCfg()
    assert cfg.breakeven_trigger_pct == 0.01
    assert cfg.breakeven_offset_pct < cfg.breakeven_trigger_pct  # net-breakeven buffer


def test_offset_equal_to_trigger_rejected():
    with pytest.raises(ValueError, match="must be < breakeven_trigger_pct"):
        RiskCfg(breakeven_trigger_pct=0.01, breakeven_offset_pct=0.01)


def test_offset_above_trigger_rejected():
    with pytest.raises(ValueError, match="must be < breakeven_trigger_pct"):
        RiskCfg(breakeven_trigger_pct=0.01, breakeven_offset_pct=0.02)


def test_offset_below_trigger_ok():
    cfg = RiskCfg(breakeven_trigger_pct=0.01, breakeven_offset_pct=0.0012)
    assert cfg.breakeven_offset_pct == 0.0012


def test_disabled_breakeven_ignores_offset():
    # trigger == 0 disables breakeven entirely → offset is irrelevant, no error
    cfg = RiskCfg(breakeven_trigger_pct=0.0, breakeven_offset_pct=0.05)
    assert cfg.breakeven_trigger_pct == 0.0


def test_negative_breakeven_rejected():
    with pytest.raises(ValueError):
        RiskCfg(breakeven_trigger_pct=-0.01)
