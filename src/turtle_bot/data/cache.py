"""Parquet-backed OHLCV cache for turtle_bot (Polars, Decimal-preserving).

Layout:  <cache_dir>/<interval>/<SYMBOL>.parquet
Schema:  ts(Datetime[us, UTC]), open/high/low/close/volume(Decimal)

Unlike ``src/bot/data/cache.py`` (pandas/float64), this keeps OHLCV as Polars
``Decimal`` end-to-end so no float artifacts enter the backtest (trading §1.2).
"""
from __future__ import annotations

from pathlib import Path

import polars as pl

OHLCV_COLUMNS = ("ts", "open", "high", "low", "close", "volume")


def _path(cache_dir: Path | str, interval: str, symbol: str) -> Path:
    return Path(cache_dir) / interval / f"{symbol}.parquet"


def read(cache_dir: Path | str, interval: str, symbol: str) -> pl.DataFrame | None:
    """Return the cached DataFrame sorted by ts, or None if not cached."""
    p = _path(cache_dir, interval, symbol)
    if not p.exists():
        return None
    return pl.read_parquet(p).sort("ts")


def write(cache_dir: Path | str, interval: str, symbol: str, df: pl.DataFrame) -> Path:
    """Merge *df* with any existing cached rows (dedupe on ts) and persist."""
    p = _path(cache_dir, interval, symbol)
    p.parent.mkdir(parents=True, exist_ok=True)

    if df.is_empty():
        return p

    existing = read(cache_dir, interval, symbol)
    if existing is not None and not existing.is_empty():
        df = pl.concat([existing, df], how="vertical")

    df = df.unique(subset=["ts"], keep="last").sort("ts")
    df.write_parquet(p, compression="snappy")
    return p
