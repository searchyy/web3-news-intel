from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from dateutil import parser as date_parser


def utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return ensure_utc(value)
    if isinstance(value, int | float):
        try:
            return datetime.fromtimestamp(value, tz=UTC)
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, tuple):
        try:
            year, month, day, hour, minute, second = value[:6]
            return datetime(year, month, day, hour, minute, second, tzinfo=UTC)
        except (TypeError, ValueError):
            return None
    if isinstance(value, str):
        try:
            return ensure_utc(parsedate_to_datetime(value))
        except (TypeError, ValueError, IndexError):
            pass
        try:
            return ensure_utc(date_parser.parse(value))
        except (TypeError, ValueError, OverflowError):
            return None
    return None
