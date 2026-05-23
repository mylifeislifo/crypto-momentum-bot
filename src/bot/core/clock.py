from datetime import datetime, timedelta, timezone

import pytz

KST = pytz.timezone("Asia/Seoul")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def kst_now() -> datetime:
    return datetime.now(KST)


def next_9am_kst() -> datetime:
    """Return the next 09:00 KST as a UTC-aware datetime."""
    now_kst = kst_now()
    today_9am = now_kst.replace(hour=9, minute=0, second=0, microsecond=0)
    if now_kst >= today_9am:
        next_9am = today_9am + timedelta(days=1)
    else:
        next_9am = today_9am
    return next_9am.astimezone(timezone.utc)


def floor_to_interval(ts: datetime, interval_sec: int) -> datetime:
    """Floor a UTC datetime to the given interval in seconds."""
    epoch = ts.timestamp()
    floored = (epoch // interval_sec) * interval_sec
    return datetime.fromtimestamp(floored, tz=timezone.utc)
