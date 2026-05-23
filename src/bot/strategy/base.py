"""Strategy base types.

StrategyContext is the unified snapshot passed to confluence.evaluate().
The aggregator owns it, updates it in place, and passes it by reference.
"""

from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from ..core.enums import Interval
from ..core.types import Bar, OBSnapshot, OIFunding, SentimentReading


@dataclass
class StrategyContext:
    latest_ob: Optional[OBSnapshot] = None
    latest_oi: Optional[OIFunding] = None
    latest_sentiment: Optional[SentimentReading] = None
    recent_bars_5m: deque[Bar] = field(default_factory=lambda: deque(maxlen=20))
    recent_bars_15m: deque[Bar] = field(default_factory=lambda: deque(maxlen=10))

    def is_ready(self, min_bars: int = 3) -> bool:
        """True when all three data sources have produced at least one reading
        and we have enough bar history to compute CVD lookback."""
        return (
            self.latest_ob is not None
            and self.latest_oi is not None
            and self.latest_sentiment is not None
            and len(self.recent_bars_5m) >= min_bars
        )

    def last_n_bars(self, n: int, interval: Interval = Interval.M5) -> list[Bar]:
        bars = self.recent_bars_5m if interval == Interval.M5 else self.recent_bars_15m
        return list(bars)[-n:]

    def ingest_bar(self, bar: Bar) -> None:
        if bar.interval == Interval.M5:
            self.recent_bars_5m.append(bar)
        elif bar.interval == Interval.M15:
            self.recent_bars_15m.append(bar)
