"""Time abstraction so backtests reuse live code paths."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone


class Clock(ABC):
    @abstractmethod
    def now(self) -> datetime: ...


class SystemClock(Clock):
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class SimClock(Clock):
    def __init__(self, start: datetime) -> None:
        self._t = start.astimezone(timezone.utc)

    def now(self) -> datetime:
        return self._t

    def set(self, t: datetime) -> None:
        self._t = t.astimezone(timezone.utc)
