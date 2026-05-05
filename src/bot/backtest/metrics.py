"""Performance metrics for backtests / paper / live equity curves."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class Metrics:
    total_return: float
    cagr: float
    sharpe: float
    sortino: float
    max_drawdown: float
    calmar: float
    win_rate: float
    profit_factor: float
    expectancy_r: float
    n_trades: int
    avg_holding_hours: float
    exposure: float

    def as_dict(self) -> dict:
        return self.__dict__


def equity_returns(equity: pd.Series, periods_per_year: float) -> pd.Series:
    return equity.pct_change().dropna()


def sharpe(returns: pd.Series, periods_per_year: float) -> float:
    if returns.std() == 0 or returns.empty:
        return 0.0
    return float(returns.mean() / returns.std() * np.sqrt(periods_per_year))


def sortino(returns: pd.Series, periods_per_year: float) -> float:
    downside = returns[returns < 0]
    dd_std = downside.std()
    if not dd_std or dd_std == 0 or returns.empty:
        return 0.0
    return float(returns.mean() / dd_std * np.sqrt(periods_per_year))


def max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    dd = (equity - peak) / peak
    return float(dd.min())


def compute(equity: pd.Series, trades: list[dict], bars_per_year: float) -> Metrics:
    """equity: pd.Series indexed by datetime (UTC). trades: list with 'pnl', 'ts', 'entry_ts'."""
    if equity.empty:
        return Metrics(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

    rets = equity_returns(equity, bars_per_year)
    total_return = float(equity.iloc[-1] / equity.iloc[0] - 1)

    span_years = max(
        1e-9, (equity.index[-1] - equity.index[0]).total_seconds() / (365.25 * 86400)
    )
    cagr = float((equity.iloc[-1] / equity.iloc[0]) ** (1 / span_years) - 1)
    sh = sharpe(rets, bars_per_year)
    so = sortino(rets, bars_per_year)
    mdd = max_drawdown(equity)
    calmar = float(cagr / abs(mdd)) if mdd < 0 else 0.0

    closing = [t for t in trades if t.get("side") == "sell" and "pnl" in t]
    pnls = [t["pnl"] for t in closing]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    win_rate = len(wins) / len(pnls) if pnls else 0.0
    profit_factor = (sum(wins) / abs(sum(losses))) if losses else (float("inf") if wins else 0.0)
    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = float(np.mean(losses)) if losses else 0.0
    expectancy = (
        win_rate * avg_win + (1 - win_rate) * avg_loss
    ) / abs(avg_loss) if avg_loss != 0 else 0.0

    holdings = []
    for t in closing:
        if t.get("entry_ts") is not None:
            entry = pd.Timestamp(t["entry_ts"])
            exit_ = pd.Timestamp(t["ts"])
            holdings.append((exit_ - entry).total_seconds() / 3600.0)
    avg_hold = float(np.mean(holdings)) if holdings else 0.0
    exposure = float((rets != 0).mean()) if not rets.empty else 0.0

    return Metrics(
        total_return=total_return,
        cagr=cagr,
        sharpe=sh,
        sortino=so,
        max_drawdown=mdd,
        calmar=calmar,
        win_rate=win_rate,
        profit_factor=profit_factor,
        expectancy_r=expectancy,
        n_trades=len(pnls),
        avg_holding_hours=avg_hold,
        exposure=exposure,
    )
