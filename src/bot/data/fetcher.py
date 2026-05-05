"""Historical OHLCV fetcher with 200-bar pagination (Upbit limit)."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pyupbit

from bot.core.enums import Interval
from bot.core.logging import get_logger

from . import cache as ohlcv_cache

log = get_logger(__name__)

_INTERVAL_MINUTES: dict[Interval, int] = {
    Interval.M1: 1,
    Interval.M5: 5,
    Interval.M15: 15,
    Interval.M60: 60,
    Interval.M240: 240,
    Interval.D1: 1440,
}

_RATE_LIMIT_SLEEP = 0.12  # seconds between requests (~8 req/s, Upbit allows ~10)


def _to_utc(df: pd.DataFrame) -> pd.DataFrame:
    """Localise KST index → UTC."""
    if df.empty:
        return df
    idx = pd.to_datetime(df.index)
    if idx.tzinfo is None:
        idx = idx.tz_localize("Asia/Seoul")
    df.index = idx.tz_convert("UTC")
    return df[["open", "high", "low", "close", "volume"]].copy()


def fetch_symbol(
    symbol: str,
    interval: Interval,
    days: int,
) -> pd.DataFrame:
    """Fetch up to *days* of OHLCV for *symbol* by paginating backwards."""
    interval_min = _INTERVAL_MINUTES.get(interval, 5)
    target_bars = days * 24 * 60 // interval_min

    frames: list[pd.DataFrame] = []
    to_dt: str | None = None  # oldest timestamp seen so far (pyupbit 'to' param)

    while len(frames) == 0 or sum(len(f) for f in frames) < target_bars:
        kwargs: dict = {"count": 200}
        if to_dt is not None:
            kwargs["to"] = to_dt

        try:
            df = pyupbit.get_ohlcv(symbol, interval=interval.value, **kwargs)
        except Exception as exc:
            log.warning("fetch_error", symbol=symbol, to=to_dt, error=str(exc))
            break

        if df is None or df.empty:
            break

        df = _to_utc(df)
        frames.append(df)

        # oldest bar becomes next 'to' (exclusive upper bound for next request)
        oldest = df.index[0]
        # subtract one interval so we don't re-fetch the same bar
        oldest_kst = oldest.tz_convert("Asia/Seoul")
        to_dt = (oldest_kst - timedelta(minutes=interval_min)).strftime("%Y-%m-%d %H:%M:%S")

        time.sleep(_RATE_LIMIT_SLEEP)

        # Stop if we've reached the target start date
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        if oldest <= cutoff:
            break

    if not frames:
        log.warning("no_data", symbol=symbol)
        return pd.DataFrame()

    combined = pd.concat(frames)
    combined = combined[~combined.index.duplicated(keep="last")].sort_index()

    # Trim to requested window
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    combined = combined[combined.index >= cutoff]

    log.info("fetched", symbol=symbol, bars=len(combined))
    return combined


def fetch_history_for_symbols(
    symbols: list[str],
    interval: Interval,
    days: int,
    cache_dir: Path,
) -> None:
    """Fetch and cache *days* of history for every symbol."""
    cache_dir = Path(cache_dir)
    total = len(symbols)
    for i, symbol in enumerate(symbols, 1):
        log.info("fetch_symbol_start", symbol=symbol, progress=f"{i}/{total}")
        try:
            df = fetch_symbol(symbol, interval, days)
            ohlcv_cache.write(cache_dir, interval, symbol, df)
        except Exception as exc:
            log.error("fetch_symbol_failed", symbol=symbol, error=str(exc))
