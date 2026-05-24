"""Offline tests for the Bitget fetcher.

Network is mocked (aioresponses) so these run without reaching api.bitget.com.
They lock in the parse contract (Decimal preservation, UTC ts) and the backward
pagination loop; the live request shape still needs a one-off check against the
real API once the network policy allows the host.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal

import polars as pl
import pytest
from aioresponses import aioresponses

from turtle_bot.data import cache as ohlcv_cache
from turtle_bot.data import fetcher
from turtle_bot.data.fetcher import BitgetAPIError, fetch_symbol, parse_candles

_URL = re.compile(r"^https://api\.bitget\.com/api/v2/mix/market/history-candles.*$")


def _ms(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, tzinfo=timezone.utc).timestamp() * 1000)


def _candle(year: int, month: int, day: int, *, o: str, h: str, lo: str, c: str, v: str):
    return [str(_ms(year, month, day)), o, h, lo, c, v, "0"]


def _ok(data: list[list[str]]) -> dict:
    return {"code": "00000", "msg": "success", "requestTime": 0, "data": data}


def test_parse_candles_preserves_decimal_and_utc() -> None:
    raw = [
        _candle(2024, 1, 1, o="27000.5", h="27200.25", lo="26900.1", c="27150.0", v="0.1"),
    ]
    df = parse_candles(raw)

    assert df.schema["open"] == pl.Decimal(precision=38, scale=12)
    assert df.schema["ts"] == pl.Datetime(time_unit="us", time_zone="UTC")

    # exact Decimal values, no float drift (0.1 stays 0.1)
    assert df["open"][0] == Decimal("27000.5")
    assert df["high"][0] == Decimal("27200.25")
    assert df["low"][0] == Decimal("26900.1")
    assert df["close"][0] == Decimal("27150.0")
    assert df["volume"][0] == Decimal("0.1")
    assert df["ts"][0] == datetime(2024, 1, 1, tzinfo=timezone.utc)


def test_parse_candles_empty_keeps_schema() -> None:
    df = parse_candles([])
    assert df.is_empty()
    assert df.schema["close"] == pl.Decimal(precision=38, scale=12)


@pytest.mark.asyncio
async def test_fetch_symbol_paginates_backwards() -> None:
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 1, 10, tzinfo=timezone.utc)

    page1 = [  # most recent batch (returned for endTime=end)
        _candle(2024, 1, d, o="100", h="110", lo="90", c="105", v="1") for d in range(5, 10)
    ]
    page2 = [  # older batch (returned for endTime=2024-01-05)
        _candle(2024, 1, d, o="100", h="110", lo="90", c="105", v="1") for d in range(1, 5)
    ]

    with aioresponses() as mock:
        mock.get(_URL, payload=_ok(page1))
        mock.get(_URL, payload=_ok(page2))
        df = await fetch_symbol("BTCUSDT", start, end)

    # 9 distinct daily bars (Jan 1..9), sorted ascending, deduped
    assert df.height == 9
    assert df["ts"][0] == datetime(2024, 1, 1, tzinfo=timezone.utc)
    assert df["ts"][-1] == datetime(2024, 1, 9, tzinfo=timezone.utc)
    assert df["ts"].is_sorted()
    assert df["close"][0] == Decimal("105")


@pytest.mark.asyncio
async def test_fetch_symbol_raises_on_api_error() -> None:
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 1, 10, tzinfo=timezone.utc)

    with aioresponses() as mock:
        mock.get(_URL, payload={"code": "40034", "msg": "param error", "data": []})
        with pytest.raises(BitgetAPIError):
            await fetch_symbol("BTCUSDT", start, end)


def test_cache_round_trip_preserves_decimal(tmp_path) -> None:
    raw = [_candle(2024, 1, 1, o="27000.5", h="27200", lo="26900", c="27150", v="0.1")]
    df = parse_candles(raw)

    path = ohlcv_cache.write(tmp_path, "1d", "BTCUSDT", df)
    assert path.exists()

    loaded = ohlcv_cache.read(tmp_path, "1d", "BTCUSDT")
    assert loaded is not None
    assert loaded.schema["open"] == pl.Decimal(precision=38, scale=12)
    assert loaded["open"][0] == Decimal("27000.5")
    assert loaded["ts"][0] == datetime(2024, 1, 1, tzinfo=timezone.utc)


def test_cache_merge_dedupes_on_ts(tmp_path) -> None:
    first = parse_candles([_candle(2024, 1, 1, o="1", h="1", lo="1", c="1", v="1")])
    # same ts, different close -> keep="last" should win on re-write
    second = parse_candles([_candle(2024, 1, 1, o="1", h="1", lo="1", c="2", v="1")])

    ohlcv_cache.write(tmp_path, "1d", "BTCUSDT", first)
    ohlcv_cache.write(tmp_path, "1d", "BTCUSDT", second)

    loaded = ohlcv_cache.read(tmp_path, "1d", "BTCUSDT")
    assert loaded is not None
    assert loaded.height == 1
    assert loaded["close"][0] == Decimal("2")


def test_module_documents_unverified_status() -> None:
    # guardrail: the "verify against live API" caveat must stay until validated
    assert "NOT been exercised against the real API" in (fetcher.__doc__ or "")
