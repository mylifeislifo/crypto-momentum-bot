"""Strategy ABC. Pure: takes a context, returns signals. No I/O."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from bot.core.enums import Regime
from bot.core.types import Position, Signal


@dataclass
class StrategyContext:
    ts: datetime
    symbol: str
    bar: pd.Series  # current bar with indicator columns attached
    history: pd.DataFrame  # history up to and including current bar
    position: Position | None
    regime: Regime
    last_exit_ts: datetime | None  # for re-entry cooldown
    meta: dict = field(default_factory=dict)


class Strategy(ABC):
    @abstractmethod
    def on_bar(self, ctx: StrategyContext) -> list[Signal]: ...
