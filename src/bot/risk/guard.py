"""Daily/weekly loss limits and MDD kill switch."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum


class GuardState(str, Enum):
    OK = "ok"
    DAILY_HALT = "daily_halt"  # no new entries today
    WEEKLY_HALT = "weekly_halt"  # no new entries this week
    KILLED = "killed"  # everything liquidated, manual restart required


@dataclass
class Guard:
    daily_loss_limit: float  # negative number, e.g. -0.03
    weekly_loss_limit: float
    mdd_killswitch: float
    peak_equity: Decimal = Decimal(0)
    day_start_equity: Decimal = Decimal(0)
    week_start_equity: Decimal = Decimal(0)
    current_day: str = ""
    current_week: str = ""
    state: GuardState = GuardState.OK
    history: list = field(default_factory=list)

    def update(self, ts: datetime, equity: Decimal) -> GuardState:
        # Initialize period anchors
        day = ts.strftime("%Y-%m-%d")
        week = f"{ts.isocalendar().year}-W{ts.isocalendar().week:02d}"
        if day != self.current_day:
            self.current_day = day
            self.day_start_equity = equity
            if self.state is GuardState.DAILY_HALT:
                self.state = GuardState.OK
        if week != self.current_week:
            self.current_week = week
            self.week_start_equity = equity
            if self.state is GuardState.WEEKLY_HALT:
                self.state = GuardState.OK

        if equity > self.peak_equity:
            self.peak_equity = equity

        if self.state is GuardState.KILLED:
            return self.state

        # Check kill switch first (most severe)
        if self.peak_equity > 0:
            mdd = float((equity - self.peak_equity) / self.peak_equity)
            if mdd <= self.mdd_killswitch:
                self.state = GuardState.KILLED
                return self.state

        # Daily and weekly halts
        if self.day_start_equity > 0:
            dpnl = float((equity - self.day_start_equity) / self.day_start_equity)
            if dpnl <= self.daily_loss_limit:
                self.state = GuardState.DAILY_HALT
                return self.state
        if self.week_start_equity > 0:
            wpnl = float((equity - self.week_start_equity) / self.week_start_equity)
            if wpnl <= self.weekly_loss_limit:
                self.state = GuardState.WEEKLY_HALT
                return self.state

        return self.state

    def can_enter(self) -> bool:
        return self.state is GuardState.OK

    def must_liquidate(self) -> bool:
        return self.state is GuardState.KILLED
