from datetime import datetime, timezone

import numpy as np
import pandas as pd

from bot.core.enums import Regime
from bot.data.indicators import add_all
from bot.strategy.base import StrategyContext
from bot.strategy.momentum import MomentumTrendStrategy


PARAMS = {
    "ema_fast": 5,
    "ema_slow": 10,
    "atr_period": 5,
    "adx_period": 5,
    "adx_min": 10.0,
    "donchian_period": 5,
    "vol_ratio_min": 1.0,
    "momentum_lookback": 3,
    "reentry_cooldown_hours": 24,
}


def _frame(prices, vols=None):
    n = len(prices)
    idx = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    if vols is None:
        vols = [10.0] * n
    df = pd.DataFrame({
        "open": prices,
        "high": [p + 0.5 for p in prices],
        "low": [p - 0.5 for p in prices],
        "close": prices,
        "volume": vols,
    }, index=idx)
    return df


def test_no_signal_during_warmup():
    df = _frame([100.0] * 5)
    df = add_all(df, PARAMS)
    s = MomentumTrendStrategy(PARAMS)
    ctx = StrategyContext(
        ts=df.index[-1].to_pydatetime(),
        symbol="KRW-BTC",
        bar=df.iloc[-1],
        history=df,
        position=None,
        regime=Regime.RISK_ON,
        last_exit_ts=None,
    )
    sigs = s.on_bar(ctx)
    assert sigs == []


def test_breakout_generates_entry():
    # Build trending price + volume spike at the end
    n = 60
    base = list(np.linspace(100, 100, 30)) + list(np.linspace(101, 130, 30))
    vols = [10.0] * (n - 1) + [50.0]
    df = _frame(base, vols)
    df = add_all(df, PARAMS)
    s = MomentumTrendStrategy(PARAMS)
    ctx = StrategyContext(
        ts=df.index[-1].to_pydatetime(),
        symbol="KRW-BTC",
        bar=df.iloc[-1],
        history=df,
        position=None,
        regime=Regime.RISK_ON,
        last_exit_ts=None,
    )
    sigs = s.on_bar(ctx)
    assert any(sig.enter for sig in sigs)


def test_regime_off_blocks_entries():
    n = 60
    base = list(np.linspace(100, 100, 30)) + list(np.linspace(101, 130, 30))
    vols = [10.0] * (n - 1) + [50.0]
    df = _frame(base, vols)
    df = add_all(df, PARAMS)
    s = MomentumTrendStrategy(PARAMS)
    ctx = StrategyContext(
        ts=df.index[-1].to_pydatetime(),
        symbol="KRW-BTC",
        bar=df.iloc[-1],
        history=df,
        position=None,
        regime=Regime.RISK_OFF,
        last_exit_ts=None,
    )
    sigs = s.on_bar(ctx)
    assert sigs == []
