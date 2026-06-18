from __future__ import annotations

import httpx
import pytest

from app.core.errors import AccessDeniedError, RobotsDisallowedError
from app.fetch.client import FetchClient
from app.fetch.rate_limit import HostRateLimiter


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
