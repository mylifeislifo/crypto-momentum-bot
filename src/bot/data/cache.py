"""Parquet-backed OHLCV cache.

Layout:  <cache_dir>/<interval.value>/<SYMBOL>.parquet
Index:   DatetimeIndex UTC, freq not enforced (gaps OK)
Columns: open, high, low, close, volume  (float64)
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from bot.core.enums import Interval


def _path(cache_dir: Path, interval: Interval, symbol: str) -> Path:
    return Path(cache_dir) / interval.value / f"{symbol}.parquet"


def read(cache_dir: Path, interval: Interval, symbol: str) -> pd.DataFrame:
    """Return DataFrame or empty DataFrame if not cached."""
    p = _path(cache_dir, interval, symbol)
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_parquet(p)
    if df.index.tzinfo is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    return df.sort_index()


def write(cache_dir: Path, interval: Interval, symbol: str, df: pd.DataFrame) -> None:
    """Merge *df* with any existing cached rows and persist."""
    if df.empty:
        return
    p = _path(cache_dir, interval, symbol)
    p.parent.mkdir(parents=True, exist_ok=True)

    existing = read(cache_dir, interval, symbol)
    if not existing.empty:
        df = pd.concat([existing, df])
        df = df[~df.index.duplicated(keep="last")].sort_index()

    # Ensure UTC before writing
    if df.index.tzinfo is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")

    df.to_parquet(p, engine="pyarrow", compression="snappy")
