"""Offline replay: feed historical Binance kline data through confluence engine.

Downloads 5m + 15m klines from Binance public API (no auth needed), builds Bar
objects, synthesises OB/OI/sentiment from constants, and runs ConfluenceStrategy
to count how many LONG/SHORT signals would have fired.

Usage:
  python scripts/replay.py --days 30 --config config/default.yaml
  python scripts/replay.py --days 7 --symbol BTCUSDT --output signals.csv
"""

import argparse
import asyncio
import csv
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import aiohttp

from bot.config.loader import load_config
from bot.core.enums import Interval, SentimentLabel, Side
from bot.core.types import Bar, OBLevel, OBSnapshot, OIFunding, SentimentReading
from bot.strategy.base import StrategyContext
from bot.strategy.confluence import ConfluenceStrategy

_BINANCE_KLINE = "https://fapi.binance.com/fapi/v1/klines"
_NEUTRAL_OB = OBSnapshot(
    ts=datetime(2024, 1, 1, tzinfo=timezone.utc),
    bids=(OBLevel(price=Decimal("50000"), qty=Decimal("1")),),
    asks=(OBLevel(price=Decimal("50001"), qty=Decimal("1")),),
    imbalance_raw=0.35,
    imbalance=0.35,
    mid_price=Decimal("50000"),
    spread=Decimal("1"),
)
_NEUTRAL_OI = OIFunding(
    ts=datetime(2024, 1, 1, tzinfo=timezone.utc),
    open_interest=Decimal("10000"),
    oi_delta_pct=0.004,
    funding_rate=-0.0002,
    next_funding_ts=datetime(2024, 1, 1, tzinfo=timezone.utc),
)
_NEUTRAL_SENT = SentimentReading(
    ts=datetime(2024, 1, 1, tzinfo=timezone.utc),
    fear_greed_index=25,
    sentiment_label=SentimentLabel.FEAR,
    long_ratio=0.45,
    short_ratio=0.55,
)


async def _fetch_klines(
    session: aiohttp.ClientSession,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    limit: int = 1500,
) -> list[list]:
    rows: list[list] = []
    cur = start_ms
    while cur < end_ms:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": cur,
            "endTime": end_ms,
            "limit": limit,
        }
        async with session.get(_BINANCE_KLINE, params=params) as r:
            r.raise_for_status()
            batch = await r.json()
        if not batch:
            break
        rows.extend(batch)
        cur = int(batch[-1][0]) + 1
    return rows


def _to_bar(row: list, interval: Interval) -> Bar:
    ts = datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc)
    o, h, l, c = (Decimal(row[i]) for i in (1, 2, 3, 4))
    volume = Decimal(row[5])
    # taker_buy_base_asset_volume is index 9
    buy_vol = Decimal(row[9])
    sell_vol = volume - buy_vol
    cvd_delta = float(buy_vol - sell_vol)
    return Bar(
        ts=ts,
        interval=interval,
        open=o,
        high=h,
        low=l,
        close=c,
        volume=volume,
        buy_volume=buy_vol,
        sell_volume=sell_vol,
        cvd_delta=cvd_delta,
        cvd_cumulative=cvd_delta,
        vwap=c,
        trade_count=int(row[8]),
    )


async def run(days: int, symbol: str, config_path: str, output: str | None) -> None:
    config, _ = load_config(config_path)
    engine = ConfluenceStrategy(config.strategy, config.risk)
    min_bars = config.strategy.cvd_lookback_bars

    now = datetime.now(tz=timezone.utc)
    start = now - timedelta(days=days)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)

    print(f"Fetching {days}d of {symbol} klines from Binance Futures...")

    async with aiohttp.ClientSession() as session:
        rows_5m = await _fetch_klines(session, symbol, "5m", start_ms, end_ms)
        rows_15m = await _fetch_klines(session, symbol, "15m", start_ms, end_ms)

    bars_5m = [_to_bar(r, Interval.M5) for r in rows_5m]
    bars_15m = [_to_bar(r, Interval.M15) for r in rows_15m]

    print(f"  5m bars : {len(bars_5m)}")
    print(f"  15m bars: {len(bars_15m)}")

    idx_15 = 0
    signals: list[dict] = []
    ctx = StrategyContext()
    ctx.latest_ob = _NEUTRAL_OB
    ctx.latest_oi = _NEUTRAL_OI
    ctx.latest_sentiment = _NEUTRAL_SENT

    for bar in bars_5m:
        # advance 15m bars up to this timestamp
        while idx_15 < len(bars_15m) and bars_15m[idx_15].ts <= bar.ts:
            ctx.ingest_bar(bars_15m[idx_15])
            idx_15 += 1

        ctx.ingest_bar(bar)

        if not ctx.is_ready(min_bars):
            continue

        sig = engine.evaluate(ctx)
        if sig is not None:
            signals.append(
                {
                    "ts": bar.ts.isoformat(),
                    "side": sig.side.value,
                    "entry": str(sig.entry_price_est),
                    "stop": str(sig.stop_price),
                    "confidence": f"{sig.confidence:.3f}",
                    "cvd_sum": f"{sum(b.cvd_delta for b in ctx.recent_bars_5m):.2f}",
                }
            )

    longs = sum(1 for s in signals if s["side"] == Side.LONG.value)
    shorts = sum(1 for s in signals if s["side"] == Side.SHORT.value)

    print(f"\nSignals over {days} days: {len(signals)} total  ({longs} LONG / {shorts} SHORT)")
    if signals:
        print(f"  First: {signals[0]['ts']}  {signals[0]['side']}  conf={signals[0]['confidence']}")
        print(f"  Last : {signals[-1]['ts']}  {signals[-1]['side']}  conf={signals[-1]['confidence']}")

    if output:
        with open(output, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["ts", "side", "entry", "stop", "confidence", "cvd_sum"])
            writer.writeheader()
            writer.writerows(signals)
        print(f"\nSaved to {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--config", default="config/default.yaml")
    parser.add_argument("--output", default=None, help="CSV output path")
    args = parser.parse_args()

    asyncio.run(run(args.days, args.symbol, args.config, args.output))
