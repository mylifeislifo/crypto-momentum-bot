from datetime import datetime, timedelta, timezone
from decimal import Decimal

from bot.risk.guard import Guard, GuardState


def test_daily_halt_triggers_and_resets_next_day():
    g = Guard(daily_loss_limit=-0.03, weekly_loss_limit=-0.10, mdd_killswitch=-0.25)
    t = datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc)
    g.update(t, Decimal("1000000"))
    g.update(t + timedelta(hours=1), Decimal("960000"))  # -4%
    assert g.state is GuardState.DAILY_HALT
    next_day = datetime(2024, 1, 2, 9, 0, tzinfo=timezone.utc)
    g.update(next_day, Decimal("960000"))
    assert g.state is GuardState.OK


def test_killswitch_persists():
    g = Guard(daily_loss_limit=-0.03, weekly_loss_limit=-0.10, mdd_killswitch=-0.25)
    t = datetime(2024, 1, 1, tzinfo=timezone.utc)
    g.update(t, Decimal("1000000"))
    g.update(t + timedelta(days=1), Decimal("740000"))  # -26% from peak
    assert g.state is GuardState.KILLED
    g.update(t + timedelta(days=10), Decimal("1500000"))
    assert g.state is GuardState.KILLED


def test_can_enter_when_ok():
    g = Guard(daily_loss_limit=-0.03, weekly_loss_limit=-0.10, mdd_killswitch=-0.25)
    g.update(datetime(2024, 1, 1, tzinfo=timezone.utc), Decimal("1000000"))
    assert g.can_enter() is True
