"""Momentum/trend long-only strategy. Pure signal generator.

Entry (all required):
  1) regime == RISK_ON
  2) ema_fast > ema_slow AND close > ema_fast
  3) close >= donchian_high (prior-N high breakout)
  4) adx >= adx_min AND vol_ratio >= vol_ratio_min
  5) ret_n > 0
  6) outside reentry cooldown window for this symbol

Exit (any one):
  - close < ema_slow (trend break)
  - regime == RISK_OFF AND close < ema_fast
  - chandelier trail or initial ATR stop are managed by risk module
  - time stop is managed by risk module
"""
from __future__ import annotations

from datetime import timedelta

from bot.core.enums import Regime, Side
from bot.core.types import Signal

from .base import Strategy, StrategyContext


class MomentumTrendStrategy(Strategy):
    def __init__(self, params: dict) -> None:
        self.p = params

    def on_bar(self, ctx: StrategyContext) -> list[Signal]:
        signals: list[Signal] = []
        bar = ctx.bar

        # Skip until indicators are ready
        required = ("ema_fast", "ema_slow", "atr", "adx", "donchian_high", "vol_ratio", "ret_n")
        if any(bar.get(c) is None or _is_nan(bar.get(c)) for c in required):
            return signals

        # ---- exits first (priority over entries on same bar) ----
        if ctx.position is not None:
            close = float(bar["close"])
            ema_slow = float(bar["ema_slow"])
            ema_fast = float(bar["ema_fast"])
            if close < ema_slow:
                signals.append(_exit(ctx, "trend_break"))
                return signals
            if ctx.regime is Regime.RISK_OFF and close < ema_fast:
                signals.append(_exit(ctx, "regime_off_below_fast"))
                return signals
            return signals  # holding; risk module handles stops/time

        # ---- entry only when flat ----
        if ctx.regime is not Regime.RISK_ON:
            return signals
        if not (
            float(bar["ema_fast"]) > float(bar["ema_slow"])
            and float(bar["close"]) > float(bar["ema_fast"])
        ):
            return signals
        if not (float(bar["close"]) >= float(bar["donchian_high"])):
            return signals
        if not (
            float(bar["adx"]) >= float(self.p["adx_min"])
            and float(bar["vol_ratio"]) >= float(self.p["vol_ratio_min"])
        ):
            return signals
        if not (float(bar["ret_n"]) > 0):
            return signals
        if ctx.last_exit_ts is not None:
            cooldown = timedelta(hours=float(self.p["reentry_cooldown_hours"]))
            if (ctx.ts - ctx.last_exit_ts) < cooldown:
                return signals

        signals.append(
            Signal(
                symbol=ctx.symbol,
                ts=ctx.ts,
                side=Side.LONG,
                strength=float(bar["adx"]),
                reason="momentum_breakout",
                enter=True,
                meta={"atr": float(bar["atr"]), "ret_n": float(bar["ret_n"])},
            )
        )
        return signals


def _is_nan(x) -> bool:
    try:
        return x != x  # NaN != NaN
    except Exception:
        return False


def _exit(ctx: StrategyContext, reason: str) -> Signal:
    return Signal(
        symbol=ctx.symbol,
        ts=ctx.ts,
        side=Side.LONG,
        strength=0.0,
        reason=reason,
        enter=False,
    )
