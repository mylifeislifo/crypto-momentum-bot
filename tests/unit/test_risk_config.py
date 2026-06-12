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


# --- time stop ---

def test_time_stop_defaults_disabled_in_schema():
    cfg = RiskCfg()                       # default.yaml enables (48); schema default off
    assert cfg.time_stop_bars == 0
    assert cfg.max_hold_bars == 0


def test_negative_time_stop_rejected():
    with pytest.raises(ValueError):
        RiskCfg(time_stop_bars=-1)
    with pytest.raises(ValueError):
        RiskCfg(max_hold_bars=-5)


def test_max_hold_below_time_stop_rejected():
    with pytest.raises(ValueError, match="max_hold_bars must be >= time_stop_bars"):
        RiskCfg(time_stop_bars=48, max_hold_bars=10)


def test_time_stop_config_valid_combos():
    assert RiskCfg(time_stop_bars=48, max_hold_bars=0).time_stop_bars == 48   # cap disabled ok
    assert RiskCfg(time_stop_bars=48, max_hold_bars=576).max_hold_bars == 576  # cap above ok


def test_time_stop_requires_breakeven_enabled():
    # the proven-winner exemption needs breakeven; without it the time stop would
    # cut winners too. Disallowed — steer the user to max_hold_bars instead.
    with pytest.raises(ValueError, match="time_stop_bars requires breakeven"):
        RiskCfg(time_stop_bars=48, breakeven_trigger_pct=0.0)


def test_max_hold_allowed_without_breakeven():
    # an unconditional hard cap cuts everyone regardless of proof → no breakeven needed
    cfg = RiskCfg(time_stop_bars=0, max_hold_bars=100, breakeven_trigger_pct=0.0)
    assert cfg.max_hold_bars == 100


def test_flatten_on_shutdown_defaults_to_hold():
    assert RiskCfg().flatten_on_shutdown is False   # hold positions across restart by default
