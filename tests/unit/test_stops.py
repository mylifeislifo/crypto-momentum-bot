from datetime import datetime, timedelta, timezone
from decimal import Decimal

from bot.core.enums import Side
from bot.core.types import Position
from bot.risk.stops import StopReason, check_stops, update_trail


def _pos(entry: float = 100.0, stop: float = 95.0, hwm: float = 100.0) -> Position:
    ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return Position(
        symbol="KRW-BTC",
        side=Side.LONG,
        qty=Decimal("1"),
        entry_price=Decimal(str(entry)),
        entry_ts=ts,
        initial_stop=Decimal(str(stop)),
        trail_stop=Decimal(str(stop)),
        high_watermark=Decimal(str(hwm)),
    )


def test_trail_monotonic_non_decreasing():
    pos = _pos(entry=100, stop=95, hwm=110)
    s1 = update_trail(pos, Decimal("2"), 3.0)  # 110 - 6 = 104
    assert s1 == Decimal("104")
    pos.high_watermark = Decimal("108")  # pull back, hwm not increased
    s2 = update_trail(pos, Decimal("3"), 3.0)  # candidate = 108 - 9 = 99 < 104
    assert s2 == Decimal("104")


def test_initial_stop_triggers():
    pos = _pos(entry=100, stop=95, hwm=100)
    r = check_stops(pos, bar_low=Decimal("94"), bar_close=Decimal("96"),
                    now=pos.entry_ts + timedelta(hours=1), time_stop_hours=48.0)
    assert r is StopReason.INITIAL_STOP


def test_trail_stop_triggers():
    pos = _pos(entry=100, stop=95, hwm=120)
    update_trail(pos, Decimal("2"), 3.0)  # trail = 114
    r = check_stops(pos, bar_low=Decimal("113"), bar_close=Decimal("115"),
                    now=pos.entry_ts + timedelta(hours=1), time_stop_hours=48.0)
    assert r is StopReason.TRAIL_STOP


def test_time_stop_only_when_no_progress():
    pos = _pos(entry=100, stop=95, hwm=104)  # hwm < entry + 1R = 105
    r = check_stops(pos, bar_low=Decimal("99"), bar_close=Decimal("101"),
                    now=pos.entry_ts + timedelta(hours=49), time_stop_hours=48.0)
    assert r is StopReason.TIME_STOP


def test_time_stop_skipped_when_progress_made():
    pos = _pos(entry=100, stop=95, hwm=110)  # >= entry + 1R
    r = check_stops(pos, bar_low=Decimal("106"), bar_close=Decimal("108"),
                    now=pos.entry_ts + timedelta(hours=49), time_stop_hours=48.0)
    assert r is StopReason.NONE
