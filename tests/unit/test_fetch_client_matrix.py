from __future__ import annotations

import asyncio
import gzip
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.core.errors import (
    AccessDeniedError,
    FetchError,
    InvalidContentTypeError,
    ResponseTooLargeError,
    RobotsDisallowedError,
)
from app.core.url_security import redact_url
from app.fetch.client import FetchClient
from app.fetch.rate_limit import HostRateLimiter

TRANSIENT_STATUSES = [408, 425, 429, 500, 502, 503, 504]


@pytest.mark.parametrize("status_code", TRANSIENT_STATUSES)
async def test_transient_statuses_are_retried(status_code: int, monkeypatch) -> None:
    calls = 0

    async def fake_sleep(delay: float) -> None:
        assert delay >= 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(status_code, text="retry", request=request)
        return httpx.Response(200, text="ok", request=request)

    monkeypatch.setattr("app.fetch.client.asyncio.sleep", fake_sleep)
    fetcher = _fetcher(handler, max_retries=1)
    response = await fetcher.get_text("https://example.com/feed")
    assert response.text == "ok"
    assert calls == 2


async def test_retry_after_http_date_is_used(monkeypatch) -> None:
    sleeps: list[float] = []
    retry_at = datetime.now(UTC) + timedelta(seconds=5)
    calls = 0

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": retry_at.strftime("%a, %d %b %Y %H:%M:%S GMT")},
                request=request,
            )
        return httpx.Response(200, text="ok", request=request)

    monkeypatch.setattr("app.fetch.client.asyncio.sleep", fake_sleep)
    fetcher = _fetcher(handler, max_retries=1)
    await fetcher.get_text("https://example.com/feed")
    assert 0 <= sleeps[0] <= 5


@pytest.mark.parametrize("status_code", [401, 403])
async def test_access_denied_is_terminal(status_code: int) -> None:
    fetcher = _fetcher(lambda request: httpx.Response(status_code, request=request), max_retries=3)
    with pytest.raises(AccessDeniedError):
        await fetcher.get_text("https://example.com/feed")


async def test_redirect_loop_and_max_redirect_count() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": str(request.url)}, request=request)

    fetcher = _fetcher(handler, max_retries=0, max_redirects=2)
    with pytest.raises(FetchError) as exc:
        await fetcher.get_text("https://example.com/a")
    assert exc.value.error_code == "redirect_loop"


async def test_valid_redirect_is_followed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/a":
            return httpx.Response(302, headers={"Location": "/b"}, request=request)
        return httpx.Response(200, text="ok", request=request)

    fetcher = _fetcher(handler, max_retries=0, max_redirects=2)
    response = await fetcher.get_text("https://example.com/a")
    assert response.url == "https://example.com/b"


async def test_redirect_to_private_network_is_blocked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"Location": "http://127.0.0.1/private"},
            request=request,
        )

    fetcher = _fetcher(handler, max_retries=0)
    with pytest.raises(FetchError) as exc:
        await fetcher.get_text("https://example.com/a")
    assert exc.value.error_code == "unsafe_url"


async def test_response_body_exceeding_max_size_is_rejected() -> None:
    fetcher = _fetcher(
        lambda request: httpx.Response(200, text="x" * 20, request=request),
        max_bytes=10,
    )
    with pytest.raises(ResponseTooLargeError):
        await fetcher.get_text("https://example.com/feed")


async def test_compressed_oversized_response_is_rejected() -> None:
    body = gzip.compress(b"x" * 100)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=body,
            headers={"Content-Encoding": "gzip"},
            request=request,
        )

    fetcher = _fetcher(handler, max_bytes=10)
    with pytest.raises(ResponseTooLargeError):
        await fetcher.get_text("https://example.com/feed")


@pytest.mark.parametrize("exc_type", [httpx.ConnectTimeout, httpx.ReadTimeout])
async def test_timeouts_are_retried_then_fail(exc_type: type[Exception], monkeypatch) -> None:
    async def fake_sleep(delay: float) -> None:
        assert delay >= 0

    def handler(request: httpx.Request) -> httpx.Response:
        raise exc_type("timeout", request=request)

    monkeypatch.setattr("app.fetch.client.asyncio.sleep", fake_sleep)
    fetcher = _fetcher(handler, max_retries=1)
    with pytest.raises(FetchError) as exc:
        await fetcher.get_text("https://example.com/feed")
    assert exc.value.error_code == "transport_error"


async def test_invalid_content_type_is_rejected() -> None:
    fetcher = _fetcher(
        lambda request: httpx.Response(
            200,
            text="binary",
            headers={"Content-Type": "application/octet-stream"},
            request=request,
        ),
        max_retries=0,
    )
    with pytest.raises(InvalidContentTypeError):
        await fetcher.get_text(
            "https://example.com/feed", allowed_content_types=("application/json",)
        )


async def test_malformed_url_is_rejected() -> None:
    fetcher = _fetcher(lambda request: httpx.Response(200, request=request), max_retries=0)
    with pytest.raises(FetchError) as exc:
        await fetcher.get_text("not a url")
    assert exc.value.error_code == "malformed_url"


async def test_robots_allow_disallow_and_failure_policy() -> None:
    def allow_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /", request=request)
        return httpx.Response(200, text="ok", request=request)

    allow_fetcher = _fetcher(allow_handler, max_retries=0)
    response = await allow_fetcher.get_text("https://example.com/page", respect_robots=True)
    assert response.text == "ok"

    def disallow_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private", request=request)
        return httpx.Response(200, text="blocked", request=request)

    disallow_fetcher = _fetcher(disallow_handler, max_retries=0)
    with pytest.raises(RobotsDisallowedError):
        await disallow_fetcher.get_text("https://example.com/private/page", respect_robots=True)

    def failure_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            raise httpx.ReadTimeout("robots timeout", request=request)
        return httpx.Response(200, text="allowed by failure policy", request=request)

    failure_fetcher = _fetcher(failure_handler, max_retries=0)
    response = await failure_fetcher.get_text("https://example.com/page", respect_robots=True)
    assert response.text == "allowed by failure policy"


async def test_independent_host_rate_limits(monkeypatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("app.fetch.rate_limit.asyncio.sleep", fake_sleep)
    limiter = HostRateLimiter(10)
    await limiter.wait("https://one.example/a")
    await limiter.wait("https://two.example/a")
    await limiter.wait("https://one.example/b")
    assert len(sleeps) == 1


async def test_cancellation_during_backoff_is_not_swallowed(monkeypatch) -> None:
    async def fake_sleep(delay: float) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr("app.fetch.client.asyncio.sleep", fake_sleep)
    fetcher = _fetcher(
        lambda request: httpx.Response(429, headers={"Retry-After": "1"}, request=request),
        max_retries=1,
    )
    with pytest.raises(asyncio.CancelledError):
        await fetcher.get_text("https://example.com/feed")


def test_retry_logging_url_redaction() -> None:
    redacted = redact_url("https://example.com/feed?token=secret&apikey=abc")
    assert "secret" not in redacted
    assert "apikey" not in redacted
    assert redacted == "https://example.com/feed?redacted=1"


def _fetcher(
    handler,
    *,
    max_retries: int = 0,
    max_redirects: int = 5,
    max_bytes: int = 1024,
) -> FetchClient:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return FetchClient(
        client=client,
        rate_limiter=HostRateLimiter(0),
        max_retries=max_retries,
        max_redirects=max_redirects,
        max_response_bytes=max_bytes,
    )
