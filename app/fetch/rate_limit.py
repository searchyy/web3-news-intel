from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from urllib.parse import urlparse


class HostRateLimiter:
    def __init__(self, min_interval_seconds: float = 1.0):
        self.min_interval_seconds = min_interval_seconds
        self._last_seen: dict[str, float] = {}
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def wait(self, url: str) -> None:
        host = urlparse(url).netloc.lower()
        if not host:
            return
        async with self._locks[host]:
            now = time.monotonic()
            last_seen = self._last_seen.get(host)
            if last_seen is not None:
                delay = self.min_interval_seconds - (now - last_seen)
                if delay > 0:
                    await asyncio.sleep(delay)
            self._last_seen[host] = time.monotonic()
