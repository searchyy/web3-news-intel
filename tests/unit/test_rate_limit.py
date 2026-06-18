from __future__ import annotations

from app.fetch.rate_limit import HostRateLimiter


async def test_host_rate_limiter_waits_per_host(monkeypatch) -> None:
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("app.fetch.rate_limit.asyncio.sleep", fake_sleep)
    limiter = HostRateLimiter(min_interval_seconds=10)
    await limiter.wait("https://example.com/a")
    await limiter.wait("https://example.com/b")
    assert delays
    assert delays[0] > 9
