"""End-to-end: synthetic cache -> run_from_cache -> trades.jsonl + summary.json.

No network: writes a tiny Polars cache and drives the full runner. Also bakes in
the 신뢰성0 cross-check — re-parse the trade log and confirm Σpnl == final − initial.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import numpy as np
import polars as pl

from turtle_bot import backtest
from turtle_bot.config import CONFIG_M1, PARAMS_M1
from turtle_bot.data import cache as ohlcv_cache

_DEC = pl.Decimal(precision=38, scale=12)


def _bars(closes: list[float]) -> pl.DataFrame:
    n = len(closes)
    ts = [datetime(2020, 1, 1, tzinfo=timezone.utc) + timedelta(days=i) for i in range(n)]
    c = [Decimal(str(x)) for x in closes]
    hi = [v * Decimal("1.005") for v in c]
    lo = [v * Decimal("0.995") for v in c]
    return pl.DataFrame(
        {"ts": ts, "open": c, "high": hi, "low": lo, "close": c, "volume": [Decimal("100")] * n},
        schema_overrides={k: _DEC for k in ("open", "high", "low", "close", "volume")},
    )


def test_run_from_cache_end_to_end(tmp_path) -> None:
    cache_dir = tmp_path / "cache"
    out_dir = tmp_path / "out"
    # enough bars to warm up the real 200-day SMA in PARAMS_M1, then a breakout
    df = _bars([100.0] * 220 + list(np.linspace(101.0, 200.0, 40)) + [200.0] * 25)
    ohlcv_cache.write(cache_dir, "1d", "BTCUSDT", df)

    config = CONFIG_M1.model_copy(
        update={"data_cache_dir": cache_dir, "results_dir": out_dir, "symbols": ("BTCUSDT",)}
    )
    summary = backtest.run_from_cache(config=config, params=PARAMS_M1)

    trades_path = out_dir / "trades.jsonl"
    summary_path = out_dir / "summary.json"
    assert trades_path.exists() and summary_path.exists()
    assert summary.n_trades >= 1

    # 신뢰성0: independently re-parse the trade log and reconcile with the summary
    pnls = [Decimal(json.loads(line)["payload"]["pnl"]) for line in trades_path.read_text().splitlines()]
    assert len(pnls) == summary.n_trades
    assert summary.initial_equity + sum(pnls) == summary.final_equity

    written = json.loads(summary_path.read_text())
    assert written["params"]["risk_per_trade"] == "0.02"  # video value, not the dispatch's 0.01
    assert Decimal(written["total_costs"]) > 0  # costs are applied
