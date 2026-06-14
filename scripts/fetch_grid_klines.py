#!/usr/bin/env python3
"""Fetch spot OHLCV klines for the external grid-bot symbols and write CSVs.

Run on a host with network access to Binance public data (e.g. the Mac Mini)::

    python scripts/fetch_grid_klines.py --out data/grid
    # then commit & push the CSVs, or:
    python scripts/fetch_grid_klines.py --out data/grid --push

No API key required: uses the public ``data-api.binance.vision`` mirror
(falls back to ``api.binance.com``). Spot klines are used because they match the
raw symbol prices shown in the grid bots (e.g. PEPEUSDT ~2.8e-6, not the
1000PEPE futures contract); the spot/futures basis is negligible for grid-range
and volatility analysis.

Output: ``<out>/<SYMBOL>_<interval>.csv`` with header ``ts,open,high,low,close,volume``
where ``ts`` is Unix seconds (UTC).
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# The six external grid-bot symbols (spot tickers).
SYMBOLS = ["BTCUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "PEPEUSDT", "WIFUSDT"]

# (interval, limit). Binance allows up to 1000 bars per call; these fit in one.
#   1d x365 ~ 1 year, 4h x500 ~ 83 days, 1h x720 ~ 30 days.
PLAN = [("1d", 365), ("4h", 500), ("1h", 720)]

HOSTS = ["https://data-api.binance.vision", "https://api.binance.com"]


def fetch(symbol: str, interval: str, limit: int) -> list:
    """Fetch klines for one symbol/interval, trying each host in order."""
    last_err: Exception | None = None
    for host in HOSTS:
        url = f"{host}/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "grid-fetch/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
            last_err = exc
            continue
    raise RuntimeError(f"all hosts failed for {symbol} {interval}: {last_err}")


def write_csv(rows: list, path: Path) -> None:
    """Write Binance klines to CSV. Kline layout: [openTime_ms, o, h, l, c, vol, ...]."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["ts", "open", "high", "low", "close", "volume"])
        for k in rows:
            # Preserve price strings exactly as returned (no float round-trip).
            writer.writerow([int(k[0]) // 1000, k[1], k[2], k[3], k[4], k[5]])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="data/grid", help="output directory for CSVs")
    ap.add_argument("--push", action="store_true", help="git add/commit/push the CSVs after fetching")
    args = ap.parse_args(argv)

    out = Path(args.out)
    written: list[str] = []
    for sym in SYMBOLS:
        for interval, limit in PLAN:
            rows = fetch(sym, interval, limit)
            if not rows:
                print(f"WARN: {sym} {interval} returned no data", file=sys.stderr)
                continue
            path = out / f"{sym}_{interval}.csv"
            write_csv(rows, path)
            print(f"{sym} {interval}: {len(rows)} bars -> {path}")
            written.append(str(path))
            time.sleep(0.3)  # be gentle on the public mirror

    if args.push and written:
        subprocess.run(["git", "add", *written], check=True)
        subprocess.run(
            ["git", "commit", "-m", "data: grid-bot klines snapshot"], check=True
        )
        subprocess.run(["git", "push"], check=True)
        print("pushed to remote.")
    elif written:
        print("\nNext: commit & push the CSVs, e.g.")
        print(f"  git add {out} && git commit -m 'data: grid-bot klines snapshot' && git push")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
