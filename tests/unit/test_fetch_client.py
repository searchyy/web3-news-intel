from __future__ import annotations

import httpx
import pytest

from app.core.errors import (
    AccessDeniedError,
    FetchError,
    ResponseTooLargeError,
    RobotsDisallowedError,
)
from app.fetch.client import FetchClient
from app.fetch.rate_limit import HostRateLimiter


class ChunkedAsyncByteStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]):
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self) -> None:
        return None


async def test_429_uses_retry_after(monkeypatch) -> None:
    calls = 0
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, request=request)
        return httpx.Response(200, text="ok", request=request)

    monkeypatch.setattr("app.fetch.client.asyncio.sleep", fake_sleep)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fetcher = FetchClient(
        client=client,
        rate_limiter=HostRateLimiter(0),
        max_retries=1,
        backoff_base_seconds=0,
    )
    response = await fetcher.get_text("https://example.com/feed")
    assert response.text == "ok"
    assert sleeps == [2.0]
    await fetcher.aclose()


async def test_403_marks_access_denied_behavior() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fetcher = FetchClient(client=client, rate_limiter=HostRateLimiter(0), max_retries=0)
    with pytest.raises(AccessDeniedError):
        await fetcher.get_text("https://example.com/feed")
    await fetcher.aclose()


async def test_robots_disallow_blocks_html_fetch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /blocked", request=request)
        return httpx.Response(200, text="blocked", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fetcher = FetchClient(client=client, rate_limiter=HostRateLimiter(0), max_retries=0)
    with pytest.raises(RobotsDisallowedError):
        await fetcher.get_text("https://example.com/blocked/page", respect_robots=True)
    await fetcher.aclose()


async def test_trusted_proxy_mode_still_enforces_dns_validation(monkeypatch) -> None:
    import socket

    def fake_getaddrinfo(*_args, **_kwargs):
        raise socket.gaierror("deterministic dns failure")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fetcher = FetchClient(
        client=client,
        rate_limiter=HostRateLimiter(0),
        trust_env=True,
        validate_dns_rebinding=True,
    )
    with pytest.raises(FetchError):
        await fetcher.get_text("https://example.com/feed")
    await fetcher.aclose()


async def test_streaming_response_without_content_length_enforces_max_size() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        response = httpx.Response(
            200,
            stream=ChunkedAsyncByteStream([b"12345", b"67890", b"!"]),
            request=request,
        )
        assert "content-length" not in response.headers
        return response

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fetcher = FetchClient(
        client=client,
        rate_limiter=HostRateLimiter(0),
        max_retries=0,
        max_response_bytes=10,
    )
    with pytest.raises(ResponseTooLargeError):
        await fetcher.get_text("https://example.com/feed")
    await fetcher.aclose()


async def test_streaming_response_without_content_length_allows_normal_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        response = httpx.Response(
            200,
            headers={"Content-Type": "text/plain; charset=utf-8"},
            stream=ChunkedAsyncByteStream([b"hel", b"lo"]),
            request=request,
        )
        assert "content-length" not in response.headers
        return response

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fetcher = FetchClient(
        client=client,
        rate_limiter=HostRateLimiter(0),
        max_retries=0,
        max_response_bytes=10,
    )
    response = await fetcher.get_text(
        "https://example.com/feed", allowed_content_types=("text/plain",)
    )
    assert response.text == "hello"
    assert response.content_type == "text/plain; charset=utf-8"
    await fetcher.aclose()


async def test_304_not_modified_without_location_is_not_treated_as_bad_redirect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(304, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fetcher = FetchClient(
        client=client,
        rate_limiter=HostRateLimiter(0),
        max_retries=0,
        max_response_bytes=10,
    )
    response = await fetcher.get_text("https://example.com/feed")
    assert response.status_code == 304
    assert response.text == ""
    await fetcher.aclose()
