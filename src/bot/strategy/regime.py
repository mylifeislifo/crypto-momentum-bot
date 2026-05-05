"""Market regime filter using BTC 1h trend."""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from bot.core.enums import Regime

from ..data.indicators import ema


class RegimeFilter:
    def __init__(self, btc_1h: pd.DataFrame, ema_fast: int = 50, ema_slow: int = 200) -> None:
        if btc_1h.empty:
            self._table = pd.DataFrame()
            return
        df = btc_1h[["close"]].copy()
        df["ema_fast"] = ema(df["close"], ema_fast)
        df["ema_slow"] = ema(df["close"], ema_slow)
        df["risk_on"] = (df["ema_fast"] > df["ema_slow"]) & (df["close"] > df["ema_fast"])
        self._table = df

    def at(self, ts: datetime) -> Regime:
        if self._table.empty:
            return Regime.RISK_OFF
        idx = self._table.index.searchsorted(pd.Timestamp(ts), side="right") - 1
        if idx < 0:
            return Regime.RISK_OFF
        row = self._table.iloc[idx]
        if pd.isna(row["risk_on"]):
            return Regime.RISK_OFF
        return Regime.RISK_ON if bool(row["risk_on"]) else Regime.RISK_OFF
