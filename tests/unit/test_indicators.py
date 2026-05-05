import numpy as np
import pandas as pd

from bot.data.indicators import adx, atr, donchian_high, ema, rsi, true_range


def _series(values):
    idx = pd.date_range("2024-01-01", periods=len(values), freq="5min", tz="UTC")
    return pd.Series(values, index=idx, dtype=float)


def test_ema_recovers_constant_series():
    s = _series([10.0] * 50)
    e = ema(s, 10)
    assert np.allclose(e.dropna().values, 10.0)


def test_ema_increases_with_uptrend():
    s = _series(list(range(1, 51)))
    e = ema(s, 10).dropna()
    assert (e.diff().dropna() > 0).all()


def test_true_range_basic():
    high = _series([10, 11, 12])
    low = _series([9, 10, 11])
    close = _series([9.5, 10.5, 11.5])
    tr = true_range(high, low, close)
    # First bar TR = high-low only (no prev close)
    assert tr.iloc[0] == 1.0


def test_atr_positive_for_volatile_series():
    rng = np.random.default_rng(42)
    n = 200
    base = np.cumsum(rng.normal(0, 1, n)) + 100
    high = pd.Series(base + rng.uniform(0.5, 1.5, n),
                     index=pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC"))
    low = pd.Series(base - rng.uniform(0.5, 1.5, n), index=high.index)
    close = pd.Series(base, index=high.index)
    a = atr(high, low, close, 14).dropna()
    assert (a > 0).all()


def test_donchian_high_uses_only_past():
    s = _series([1, 2, 3, 4, 5, 6])
    d = donchian_high(s, 3).dropna()
    # at index 3 (4th bar), prior 3 highs are [1,2,3], max=3
    assert d.iloc[0] == 3.0


def test_rsi_bounded():
    rng = np.random.default_rng(0)
    s = _series(np.cumsum(rng.normal(0, 1, 200)) + 100)
    r = rsi(s, 14).dropna()
    assert ((r >= 0) & (r <= 100)).all()


def test_adx_non_negative():
    rng = np.random.default_rng(1)
    n = 200
    base = np.cumsum(rng.normal(0, 1, n)) + 100
    idx = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    high = pd.Series(base + 1.0, index=idx)
    low = pd.Series(base - 1.0, index=idx)
    close = pd.Series(base, index=idx)
    a = adx(high, low, close, 14).dropna()
    assert (a >= 0).all()
