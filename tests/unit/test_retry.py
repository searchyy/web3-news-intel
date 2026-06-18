from __future__ import annotations

from datetime import UTC, datetime

from app.fetch.retry import exponential_backoff, parse_retry_after


def test_parse_retry_after_seconds() -> None:
    assert parse_retry_after("3") == 3.0


def test_parse_retry_after_http_date() -> None:
    now = datetime(2026, 6, 18, 0, 0, 0, tzinfo=UTC)
    assert parse_retry_after("Thu, 18 Jun 2026 00:00:10 GMT", now=now) == 10.0


def test_exponential_backoff_caps() -> None:
    assert exponential_backoff(1, base_seconds=1, cap_seconds=3) == 1
    assert exponential_backoff(4, base_seconds=1, cap_seconds=3) == 3
