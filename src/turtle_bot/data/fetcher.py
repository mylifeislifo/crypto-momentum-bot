"""Bitget USDT-M perpetual daily OHLCV fetcher (Polars, Decimal-preserving).

Data source: Bitget v2 Mix market API (https://api.bitget.com). Public klines
require no API key. Prices/volumes arrive as strings and are parsed straight to
``Decimal`` — never ``float`` (trading §1.2 / §7.1). Timestamps are stored in UTC
(§7.3). Network calls use ``tenacity`` retry with exponential backoff (§4).

NOTE (verify before trusting): this module was written while ``api.bitget.com``
was network-blocked in the build environment, so the live request/response shape
(endpoint path, query params, candle field order) follows the Bitget v2 docs but
has NOT been exercised against the real API. ``parse_candles`` is unit-tested
offline; the pagination loop is tested with mocked HTTP (aioresponses). Once the
network policy allows ``api.bitget.com``, run a one-symbol fetch and confirm the
candle array layout matches ``_FIELD_*`` below before caching the full history.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import aiohttp
import polars as pl
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from . import cache as ohlcv_cache

log = structlog.get_logger(__name__)

BITGET_BASE = "https://api.bitget.com"
HISTORY_CANDLES_PATH = "/api/v2/mix/market/history-candles"
PRODUCT_TYPE = "usdt-futures"  # USDT-M perpetual
SUCCESS_CODE = "00000"

# Bitget candle array layout: [ts_ms, open, high, low, close, baseVol, quoteVol]
_FIELD_TS = 0
_FIELD_OPEN = 1
_FIELD_HIGH = 2
_FIELD_LOW = 3
_FIELD_CLOSE = 4
_FIELD_VOLUME = 5  # base volume

# interval -> Bitget granularity token. Daily uses the UTC-aligned variant
# ("1Dutc") so candles open at 00:00 UTC — confirmed against a live-working
# reference fetch. Plain "1D" aligns to a non-UTC session boundary.
_GRANULARITY: dict[str, str] = {"1d": "1Dutc"}

PAGE_LIMIT = 200  # history-candles max rows per request (Bitget v2)
_RATE_LIMIT_SLEEP = 0.15  # seconds between paged requests (well under rate cap)
_TIMEOUT = aiohttp.ClientTimeout(total=15)

# Fixed Decimal dtype for OHLCV columns. scale=12 covers sub-satoshi precision;
# precision=38 covers large quote volumes. Polars Decimal may cast to Float in
# some aggregations (§7.1) — the backtest engine must re-verify before order/DB.
_DEC = pl.Decimal(precision=38, scale=12)
_SCHEMA: dict[str, pl.DataType] = {
    "ts": pl.Datetime(time_unit="us", time_zone="UTC"),
    "open": _DEC,
    "high": _DEC,
    "low": _DEC,
    "close": _DEC,
    "volume": _DEC,
}


class BitgetAPIError(RuntimeError):
    """Bitget returned a non-success ``code`` in its JSON envelope."""


def _empty_frame() -> pl.DataFrame:
    return pl.DataFrame(schema=_SCHEMA)


def parse_candles(raw: list[list[str]]) -> pl.DataFrame:
    """Convert Bitget candle arrays to a Polars frame (Decimal OHLCV, UTC ts).

    Each row: ``[ts_ms, open, high, low, close, baseVolume, quoteVolume]`` with
    numeric fields as strings. Strings go to ``Decimal`` directly so no float
    rounding is ever introduced (trading §1.2).
    """
    if not raw:
        return _empty_frame()

    ts = pl.Series(
        "ts",
        [datetime.fromtimestamp(int(r[_FIELD_TS]) / 1000, tz=timezone.utc) for r in raw],
    ).cast(_SCHEMA["ts"])

    def dec_col(name: str, idx: int) -> pl.Series:
        return pl.Series(name, [Decimal(str(r[idx])) for r in raw], dtype=_DEC)

    return pl.DataFrame(
        [
            ts,
            dec_col("open", _FIELD_OPEN),
            dec_col("high", _FIELD_HIGH),
            dec_col("low", _FIELD_LOW),
            dec_col("close", _FIELD_CLOSE),
            dec_col("volume", _FIELD_VOLUME),
        ]
    ).sort("ts")


@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=2, max=16),
    retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError)),
    reraise=True,
)
async def _fetch_page(
    session: aiohttp.ClientSession,
    symbol: str,
    granularity: str,
    end_ms: int,
    limit: int = PAGE_LIMIT,
) -> list[list[str]]:
    """Fetch one page of candles older than *end_ms* (retries on network errors)."""
    params = {
        "symbol": symbol,
        "productType": PRODUCT_TYPE,
        "granularity": granularity,
        "endTime": str(end_ms),
        "limit": str(limit),
    }
    try:
        async with session.get(BITGET_BASE + HISTORY_CANDLES_PATH, params=params) as resp:
            resp.raise_for_status()
            body = await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        # trading §4: log the attempted request params on failure before retry
        log.error("bitget_fetch_failed", symbol=symbol, end_ms=end_ms, error=str(exc))
        raise

    if body.get("code") != SUCCESS_CODE:
        log.error("bitget_api_error", symbol=symbol, code=body.get("code"), msg=body.get("msg"))
        raise BitgetAPIError(f"{symbol}: code={body.get('code')} msg={body.get('msg')}")

    return body.get("data") or []


def _validate(symbol: str, df: pl.DataFrame) -> None:
    """Log bar count, span, internal day-gaps and duplicates for sanity checks."""
    n = df.height
    if n == 0:
        log.warning("bitget_no_bars", symbol=symbol)
        return
    first, last = df["ts"][0], df["ts"][-1]
    expected_days = (last - first).days + 1
    diffs = df.select(pl.col("ts").diff().dt.total_days().alias("d"))["d"].to_list()[1:]
    internal_gaps = sum(1 for d in diffs if d is not None and d > 1)
    duplicates = n - df["ts"].n_unique()
    log.info(
        "bitget_validated",
        symbol=symbol,
        bars=n,
        first=str(first),
        last=str(last),
        expected_days=expected_days,
        missing=expected_days - n,
        internal_gaps=internal_gaps,
        duplicates=duplicates,
    )
    if expected_days - n > 0:
        log.warning("bitget_missing_bars", symbol=symbol, missing=expected_days - n)


async def fetch_symbol(
    symbol: str,
    start: datetime,
    end: datetime,
    interval: str = "1d",
    session: aiohttp.ClientSession | None = None,
) -> pl.DataFrame:
    """Fetch daily OHLCV for *symbol* in [start, end] by paging backwards.

    Bitget ``history-candles`` returns at most ``PAGE_LIMIT`` rows older than
    ``endTime``; we walk the cursor back to the oldest bar of each page until we
    pass *start* (or the API runs out of history).
    """
    granularity = _GRANULARITY[interval]
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    own_session = session is None
    if session is None:
        session = aiohttp.ClientSession(timeout=_TIMEOUT)

    frames: list[pl.DataFrame] = []
    try:
        cursor = end_ms
        while cursor > start_ms:
            raw = await _fetch_page(session, symbol, granularity, cursor)
            if not raw:
                break
            frames.append(parse_candles(raw))

            earliest_ms = min(int(r[_FIELD_TS]) for r in raw)
            if earliest_ms >= cursor:  # no progress -> avoid infinite loop
                break
            cursor = earliest_ms
            if earliest_ms <= start_ms:
                break
            await asyncio.sleep(_RATE_LIMIT_SLEEP)
    finally:
        if own_session:
            await session.close()

    if not frames:
        log.warning("bitget_empty", symbol=symbol)
        return _empty_frame()

    combined = (
        pl.concat(frames, how="vertical")
        .unique(subset=["ts"], keep="last")
        .sort("ts")
        .filter((pl.col("ts") >= start) & (pl.col("ts") <= end))
    )
    _validate(symbol, combined)
    return combined


async def fetch_all(
    symbols: tuple[str, ...] | list[str],
    start: datetime,
    end: datetime,
    cache_dir: Path | str,
    interval: str = "1d",
) -> dict[str, Path]:
    """Fetch every symbol concurrently and write each to the Parquet cache."""
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        frames = await asyncio.gather(
            *(fetch_symbol(s, start, end, interval, session) for s in symbols)
        )

    paths: dict[str, Path] = {}
    for symbol, df in zip(symbols, frames, strict=True):
        if df.is_empty():
            continue
        path = ohlcv_cache.write(cache_dir, interval, symbol, df)
        paths[symbol] = path
        log.info("bitget_cached", symbol=symbol, bars=df.height, path=str(path))
    return paths


def _parse_day(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def main() -> None:  # pragma: no cover - thin CLI wrapper
    from ..config import CONFIG_M1

    parser = argparse.ArgumentParser(description="Fetch Bitget daily OHLCV into the cache.")
    parser.add_argument("--symbols", nargs="+", default=list(CONFIG_M1.symbols))
    parser.add_argument("--start", type=_parse_day, default=_parse_day("2019-09-01"))
    parser.add_argument(
        "--end", type=_parse_day, default=datetime.now(timezone.utc).strftime("%Y-%m-%d")
    )
    parser.add_argument("--cache-dir", type=Path, default=CONFIG_M1.data_cache_dir)
    parser.add_argument("--interval", default=CONFIG_M1.interval)
    args = parser.parse_args()

    end = args.end if isinstance(args.end, datetime) else _parse_day(args.end)
    paths = asyncio.run(
        fetch_all(args.symbols, args.start, end, args.cache_dir, args.interval)
    )
    for symbol, path in paths.items():
        print(f"{symbol}: cached -> {path}")


if __name__ == "__main__":  # pragma: no cover
    main()
