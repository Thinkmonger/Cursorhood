from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)


@lru_cache(maxsize=1)
def get_et_tz() -> ZoneInfo:
    try:
        return ZoneInfo("America/New_York")
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError(
            "Timezone data for America/New_York is unavailable. "
            "Install the tzdata package: pip install tzdata"
        ) from exc


def to_et(dt: datetime) -> datetime:
    et = get_et_tz()
    if dt.tzinfo is None:
        return dt.replace(tzinfo=et)
    return dt.astimezone(et)

def is_market_hours(dt: datetime | None = None) -> bool:
    dt = to_et(dt or datetime.now(get_et_tz()))
    if dt.weekday() >= 5:
        return False
    return MARKET_OPEN <= dt.time() <= MARKET_CLOSE


def next_market_open(after: datetime | None = None) -> datetime:
    """Earliest regular market open on or after `after` when outside market hours."""
    after = to_et(after or datetime.now(get_et_tz()))
    if is_market_hours(after):
        return after

    day: date = after.date()
    for _ in range(366):
        if day.weekday() < 5:
            open_dt = datetime.combine(day, MARKET_OPEN, tzinfo=get_et_tz())
            if open_dt > after:
                return open_dt
            if day == after.date() and after.time() < MARKET_OPEN:
                return open_dt
        day += timedelta(days=1)
    raise RuntimeError("Could not find next market open")


def seconds_until_next_wake(
    interval: int,
    market_hours_only: bool,
    now: datetime | None = None,
    *,
    trades_24_7: bool = False,
) -> float:
    """Seconds until the next cycle. Crypto-enabled bots never sleep to the open."""
    now = now or datetime.now(timezone.utc)
    now_et = to_et(now)
    if not market_hours_only or trades_24_7:
        return float(interval)
    if not is_market_hours(now_et):
        nmo = next_market_open(now_et)
        return max(1.0, (nmo - now_et).total_seconds())
    close_today = datetime.combine(now_et.date(), MARKET_CLOSE, tzinfo=get_et_tz())
    until_close = (close_today - now_et).total_seconds()
    return min(float(interval), max(1.0, until_close))


def compute_next_scheduled_run(
    *,
    interval: int,
    market_hours_only: bool,
    active: bool,
    now: datetime | None = None,
    trades_24_7: bool = False,
) -> datetime | None:
    if not active:
        return None
    now = now or datetime.now(timezone.utc)
    secs = seconds_until_next_wake(interval, market_hours_only, now, trades_24_7=trades_24_7)
    return now + timedelta(seconds=secs)
