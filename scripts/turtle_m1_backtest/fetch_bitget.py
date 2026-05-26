"""Bitget USDT-M futures klines fetcher."""
from __future__ import annotations
import time
from pathlib import Path

import requests
import pandas as pd

BASE = "https://api.bitget.com/api/v2/mix/market/history-candles"

def fetch_history(symbol: str, granularity: str = "1D",
                  start_ms: int = 1567296000000) -> pd.DataFrame:
    """Paginate backwards from now to start_ms. Bitget returns oldest-first within a window."""
    all_rows: list[list] = []
    cursor_ms = int(time.time() * 1000)
    while True:
        params = {
            "symbol": symbol, "productType": "usdt-futures",
            "granularity": granularity, "endTime": cursor_ms, "limit": "200",
        }
        r = requests.get(BASE, params=params, timeout=15)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data:
            break
        # data: [[ts_ms, o, h, l, c, baseVol, quoteVol], ...] ordered ascending by ts
        all_rows.extend(data)
        oldest_ms = int(data[0][0])
        if oldest_ms <= start_ms:
            break
        cursor_ms = oldest_ms - 1
        time.sleep(0.2)

    if not all_rows:
        return pd.DataFrame()
    # dedupe
    seen: set[int] = set()
    uniq: list[list] = []
    for row in all_rows:
        ts = int(row[0])
        if ts in seen:
            continue
        seen.add(ts)
        uniq.append(row)
    df = pd.DataFrame(uniq, columns=["open_time_ms", "open", "high", "low", "close",
                                      "base_vol", "quote_vol"])
    df["open_time"] = pd.to_datetime(df["open_time_ms"].astype(int), unit="ms", utc=True)
    for c in ["open", "high", "low", "close", "base_vol", "quote_vol"]:
        df[c] = df[c].astype(float)
    df = df.sort_values("open_time").reset_index(drop=True)
    return df[["open_time", "open", "high", "low", "close", "base_vol", "quote_vol"]]


if __name__ == "__main__":
    out_dir = Path("/home/claude/turtle_redo/data")
    out_dir.mkdir(parents=True, exist_ok=True)
    for symbol in ("BTCUSDT", "ETHUSDT"):
        print(f"[fetch] {symbol} ...", flush=True)
        df = fetch_history(symbol)
        out = out_dir / f"{symbol}_1d.csv"
        df.to_csv(out, index=False)
        print(f"[fetch] {symbol}: {len(df)} bars, {df['open_time'].min()} ~ {df['open_time'].max()}",
              flush=True)
