"""M1 backtest driver: load cached Bitget daily bars → run engine → write results.

Reads the Polars/Decimal parquet cache from ``turtle_bot.data`` and runs the
Donchian engine with this package's video parameters (2% risk) and costs.

Outputs (under ``BacktestConfig.results_dir``):
  trades.jsonl   one audit-log event per closed trade
  summary.json   run-level aggregates (Decimal serialised as strings)

CLI:
  python -m turtle_bot.backtest [--cache-dir ...] [--start YYYY-MM-DD] [--end ...]
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from .config import CONFIG_M1, PARAMS_M1, BacktestConfig, TurtleParams
from .data import cache as ohlcv_cache
from .engine import BacktestSummary, run_backtest, write_trades_jsonl


def load_bars(
    config: BacktestConfig,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict[str, pl.DataFrame]:
    bars: dict[str, pl.DataFrame] = {}
    for symbol in config.symbols:
        df = ohlcv_cache.read(config.data_cache_dir, config.interval, symbol)
        if df is None or df.is_empty():
            continue
        if start is not None:
            df = df.filter(pl.col("ts") >= start)
        if end is not None:
            df = df.filter(pl.col("ts") <= end)
        if not df.is_empty():
            bars[symbol] = df
    return bars


def run_from_cache(
    config: BacktestConfig = CONFIG_M1,
    params: TurtleParams = PARAMS_M1,
    start: datetime | None = None,
    end: datetime | None = None,
) -> BacktestSummary:
    bars = load_bars(config, start, end)
    if not bars:
        raise RuntimeError(
            f"no cached bars for {config.symbols} under {config.data_cache_dir} "
            "— run `python -m turtle_bot.data.fetcher` first"
        )

    summary = run_backtest(bars, params, config)

    write_trades_jsonl(summary.trades, config.results_dir / "trades.jsonl")

    payload = summary.as_summary_dict()
    payload["symbols"] = list(bars.keys())
    payload["bar_counts"] = {k: v.height for k, v in bars.items()}
    payload["params"] = {
        "entry_window": params.entry_window,
        "exit_window": params.exit_window,
        "atr_window": params.atr_window,
        "atr_stop_multiplier": str(params.atr_stop_multiplier),
        "trend_sma_window": params.trend_sma_window,
        "risk_per_trade": str(params.risk_per_trade),
        "leverage": str(config.leverage),
        "taker_fee": str(config.taker_fee),
        "slippage": str(config.slippage),
    }
    summary_path = config.results_dir / "summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary


def _parse_day(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def main() -> None:  # pragma: no cover - thin CLI wrapper
    parser = argparse.ArgumentParser(description="Turtle M1 daily backtest from cache")
    parser.add_argument("--cache-dir", type=Path, default=None)
    parser.add_argument("--start", type=_parse_day, default=None)
    parser.add_argument("--end", type=_parse_day, default=None)
    args = parser.parse_args()

    config = CONFIG_M1
    if args.cache_dir is not None:
        config = CONFIG_M1.model_copy(update={"data_cache_dir": args.cache_dir})

    summary = run_from_cache(config=config, start=args.start, end=args.end)
    print(json.dumps(summary.as_summary_dict(), indent=2))


if __name__ == "__main__":  # pragma: no cover
    main()
