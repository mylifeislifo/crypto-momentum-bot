"""Technical indicators. Pure pandas, no external TA libraries."""
from __future__ import annotations

import numpy as np
import pandas as pd


# ── primitives ──────────────────────────────────────────────────────────────

def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average (standard pandas ewm)."""
    return series.ewm(span=period, adjust=False).mean()


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """True range: max of (H-L, |H-Cprev|, |L-Cprev|). First bar = H-L."""
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    # First bar has no prev_close → use H-L
    tr.iloc[0] = high.iloc[0] - low.iloc[0]
    return tr


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """Average true range (Wilder's smoothing = EMA with span=period)."""
    tr = true_range(high, low, close)
    return tr.ewm(span=period, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI via Wilder's smoothing."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(span=period, adjust=False).mean()
    avg_loss = loss.ewm(span=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def donchian_high(high: pd.Series, period: int) -> pd.Series:
    """Donchian channel high using only PRIOR bars (shift-1 then rolling max).

    This prevents look-ahead: on bar N the value is max(high[N-period .. N-1]).
    """
    return high.shift(1).rolling(period).max()


def adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """Average Directional Index (Wilder smoothing)."""
    tr = true_range(high, low, close)

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=high.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=high.index,
    )

    atr_s = tr.ewm(span=period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(span=period, adjust=False).mean() / atr_s
    minus_di = 100 * minus_dm.ewm(span=period, adjust=False).mean() / atr_s

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(span=period, adjust=False).mean()


# ── composite ────────────────────────────────────────────────────────────────

def add_all(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Add all strategy indicators to a copy of *df*.

    Expected params keys (from StrategyParams.model_dump()):
      ema_fast, ema_slow, atr_period, adx_period, donchian_period,
      vol_ratio_min (unused here), momentum_lookback
    """
    out = df.copy()
    close = out["close"]
    high = out["high"]
    low = out["low"]
    volume = out["volume"]

    ema_fast_p: int = int(params.get("ema_fast", 20))
    ema_slow_p: int = int(params.get("ema_slow", 60))
    atr_p: int = int(params.get("atr_period", 14))
    adx_p: int = int(params.get("adx_period", 14))
    dc_p: int = int(params.get("donchian_period", 20))
    mom_p: int = int(params.get("momentum_lookback", 12))

    out["ema_fast"] = ema(close, ema_fast_p)
    out["ema_slow"] = ema(close, ema_slow_p)
    out["atr"] = atr(high, low, close, atr_p)
    out["adx"] = adx(high, low, close, adx_p)
    out["donchian_high"] = donchian_high(high, dc_p)

    # Volume ratio: current bar volume vs rolling mean (same window as ema_fast)
    vol_ma = volume.rolling(ema_fast_p).mean()
    out["vol_ratio"] = volume / vol_ma.replace(0, float("nan"))

    # n-period return (simple)
    out["ret_n"] = close.pct_change(periods=mom_p)

    return out
