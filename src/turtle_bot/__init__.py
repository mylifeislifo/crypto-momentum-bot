"""Turtle trading backtest bot (BTC + ETH daily futures, dual-direction).

Faithful code-ification of the Turtle breakout system from the reference video:
20-day entry / 11-day exit Donchian channels, ATR(20)x2 stop, 200-day SMA trend
filter, 2% risk-per-trade sizing. Lives alongside (and fully separate from) the
Confluence ``bot`` package in this repo.

Milestone tracking: M1 = single-shot backtest (point estimate only). Statistical
validation (walkforward + bootstrap CI + temporal decomposition) is deferred to
M2+ per the series lessons documented in the project knowledge base.
"""

__version__ = "0.1.0-m1.skeleton"
