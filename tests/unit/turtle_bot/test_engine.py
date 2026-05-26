"""Turtle M1 engine tests: no-lookahead, direction gate, 2% sizing, costs."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import numpy as np
import polars as pl

from turtle_bot.config import BacktestConfig, TurtleParams
from turtle_bot.engine import _entry_signal, precompute, run_backtest

# small but config-valid windows (trend_sma_window has ge=50)
_P = TurtleParams(entry_window=5, exit_window=3, atr_window=5, trend_sma_window=50)
_CFG = BacktestConfig()  # 2% risk via _P, taker 0.04%, slippage 0.05%
_ZERO_COST = BacktestConfig(taker_fee=Decimal("0"), slippage=Decimal("0"))

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


def _uptrend() -> pl.DataFrame:
    # rise then stay high (no pullback below SMA) so the regime is long-only
    return _bars([100.0] * 60 + list(np.linspace(101.0, 200.0, 40)) + [200.0] * 25)


def _downtrend() -> pl.DataFrame:
    # decline then stay low (no recovery above SMA) so the regime is short-only
    return _bars([200.0] * 60 + list(np.linspace(199.0, 100.0, 40)) + [100.0] * 25)


def test_indicators_no_lookahead() -> None:
    df = _bars([100.0 + i for i in range(100)])
    pre = precompute(df, _P)
    cutoff = 70
    pre_trunc = precompute(df.head(cutoff), _P)
    for col in ("dc_entry_high", "dc_entry_low", "dc_exit_high", "dc_exit_low", "sma", "atr"):
        full = pre[col][:cutoff].to_list()
        trunc = pre_trunc[col].to_list()
        for a, b in zip(full, trunc):
            if a is not None and b is not None:
                assert a == b, f"lookahead leak in {col}"


def test_donchian_uses_strictly_prior_bars() -> None:
    df = _bars([100.0 + (i % 7) for i in range(60)])
    pre = precompute(df, _P)
    highs = df["high"].cast(pl.Float64).to_list()
    dc = pre["dc_entry_high"].to_list()
    for i in range(_P.entry_window, len(df)):
        expected = max(highs[i - _P.entry_window : i])
        assert abs(dc[i] - expected) < 1e-9


def test_trade_fields_are_decimal() -> None:
    summary = run_backtest({"X": _uptrend()}, _P, _CFG)
    assert isinstance(summary.final_equity, Decimal)
    assert summary.n_trades >= 1
    for t in summary.trades:
        for v in (t.qty, t.entry_price, t.exit_price, t.pnl, t.cost):
            assert isinstance(v, Decimal)


def test_no_trades_in_flatline() -> None:
    summary = run_backtest({"X": _bars([100.0] * 120)}, _P, _CFG)
    assert summary.n_trades == 0
    assert summary.final_equity == Decimal("10000")


def test_entry_signal_direction_gate() -> None:
    # above SMA + upper breakout -> long ; below SMA + lower breakout -> short
    assert _entry_signal(Decimal("110"), 100.0, 105.0, 95.0) == "long"
    assert _entry_signal(Decimal("90"), 100.0, 105.0, 95.0) == "short"
    # upper breakout but BELOW SMA -> gated (no long); lower breakout ABOVE SMA -> gated
    assert _entry_signal(Decimal("110"), 120.0, 105.0, 95.0) is None
    assert _entry_signal(Decimal("90"), 80.0, 105.0, 95.0) is None
    # missing indicators (warmup) -> no signal
    assert _entry_signal(Decimal("100"), None, 105.0, 95.0) is None


def test_direction_gate_long_only_above_sma() -> None:
    summary = run_backtest({"BTCUSDT": _uptrend()}, _P, _CFG)
    sides = {t.side for t in summary.trades}
    assert "long" in sides
    assert "short" not in sides  # below-SMA shorts must be gated out in an uptrend


def test_direction_gate_short_only_below_sma() -> None:
    summary = run_backtest({"BTCUSDT": _downtrend()}, _P, _CFG)
    sides = {t.side for t in summary.trades}
    assert "short" in sides
    assert "long" not in sides


def test_risk_2pct_doubles_1pct_size() -> None:
    data = {"BTCUSDT": _uptrend()}
    s2 = run_backtest(data, _P.model_copy(update={"risk_per_trade": Decimal("0.02")}), _ZERO_COST)
    s1 = run_backtest(data, _P.model_copy(update={"risk_per_trade": Decimal("0.01")}), _ZERO_COST)
    ratio = s2.trades[0].qty / s1.trades[0].qty
    assert Decimal("1.9") < ratio < Decimal("2.1")


def test_costs_reduce_pnl() -> None:
    data = {"BTCUSDT": _uptrend()}
    with_costs = run_backtest(data, _P, _CFG)
    without = run_backtest(data, _P, _ZERO_COST)

    assert with_costs.total_costs > 0
    assert without.total_costs == 0
    assert with_costs.final_equity < without.final_equity
    for t in with_costs.trades:
        sign = Decimal(1) if t.side == "long" else Decimal(-1)
        gross = (t.exit_price - t.entry_price) * t.qty * sign
        assert t.pnl == gross - t.cost
        assert t.cost > 0


def test_leverage_notional_cap_not_exceeded() -> None:
    # reconstruct running equity (single symbol, one position at a time) and
    # assert each entry's notional stays within equity-at-entry × leverage
    summary = run_backtest({"BTCUSDT": _uptrend()}, _P, _CFG)
    assert summary.trades
    equity = _CFG.initial_capital  # single symbol -> full slice
    for t in summary.trades:
        assert t.entry_price * t.qty <= equity * _CFG.leverage
        equity += t.pnl


def test_jsonl_event_envelope() -> None:
    summary = run_backtest({"BTCUSDT": _uptrend()}, _P, _CFG)
    assert summary.trades
    ev = summary.trades[0].as_event()
    assert ev["source"] == "turtle_m1"
    assert ev["event"] == "trade_closed"
    assert ev["level"] == "INFO"
    for key in ("symbol", "side", "qty", "entry", "exit", "pnl", "cost"):
        assert isinstance(ev["payload"][key], str)
