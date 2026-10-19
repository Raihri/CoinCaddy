"""Date/time contract.

Event timestamps are stored in UTC. Business dates (the day an amount counts for) are calendar
dates in the business's explicit IANA timezone, stored separately from the UTC timestamp.
"""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class TimezoneError(ValueError):
    pass


def require_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise TimezoneError(f"Unknown IANA timezone: {name!r}") from exc


def business_date(event_utc: datetime, business_timezone: str) -> date:
    if event_utc.tzinfo is None:
        raise TimezoneError("event timestamp must be timezone-aware (UTC)")
    return event_utc.astimezone(require_timezone(business_timezone)).date()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
