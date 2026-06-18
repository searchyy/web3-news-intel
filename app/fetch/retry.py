from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

TRANSIENT_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return max(0.0, float(value))
    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)
    now = now or datetime.now(UTC)
    return max(0.0, (retry_at.astimezone(UTC) - now.astimezone(UTC)).total_seconds())


def exponential_backoff(
    attempt: int, *, base_seconds: float = 0.5, cap_seconds: float = 30.0
) -> float:
    return min(cap_seconds, base_seconds * (2 ** max(0, attempt - 1)))
