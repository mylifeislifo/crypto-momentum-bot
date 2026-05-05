"""End-to-end backtest with synthetic data — validates the full pipeline runs."""
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

from bot.config.schema import AppConfig, BacktestCfg, DataCfg
from bot.core.enums import Interval
from bot.data import cache as ohlcv_cache
from bot.backtest.runner import run_backtest


def _make_synth(symbol: str, start: str, n: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="5min", tz="UTC")
    drift = np.linspace(0, 0.0002, n)
    noise = rng.normal(0, 0.001, n)
    rets = drift + noise
    close = 100 * np.exp(np.cumsum(rets))
    high = close * (1 + np.abs(rng.normal(0, 0.0007, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.0007, n)))
    open_ = np.concatenate([[close[0]], close[:-1]])
    vol = rng.uniform(50, 200, n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": vol},
        index=idx,
    )


def test_backtest_runs_end_to_end(tmp_path: Path):
    cache_dir = tmp_path / "data"
    interval = Interval.M5
    symbols = ["KRW-BTC", "KRW-ETH"]
    for i, s in enumerate(symbols):
        df = _make_synth(s, "2024-01-01", n=2000, seed=i + 1)
        ohlcv_cache.write(cache_dir, interval, s, df)

    # Regime data (1h BTC)
    btc_1h = _make_synth("KRW-BTC", "2024-01-01", n=400, seed=99).resample("1h").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()
    ohlcv_cache.write(cache_dir, Interval.M60, "KRW-BTC", btc_1h)

    cfg = AppConfig(
        data=DataCfg(interval=Interval.M5, cache_dir=str(cache_dir)),
        backtest=BacktestCfg(start="2024-01-01", end="2024-01-31", initial_equity=10_000_000),
    )
    result = run_backtest(cfg, symbols=symbols)
    assert result.equity_curve.size > 0
    # Equity should remain finite, positive, near initial (no catastrophic blowup)
    assert result.equity_curve.iloc[-1] > 0
    assert np.isfinite(result.metrics.total_return)
