from __future__ import annotations

import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx

from app.fetch.user_agent import default_headers


@dataclass(slots=True)
class RobotsEntry:
    parser: urllib.robotparser.RobotFileParser
    fetched: bool


class RobotsCache:
    def __init__(self, *, user_agent: str, timeout_seconds: float = 10.0):
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self._cache: dict[str, RobotsEntry] = {}

    async def allowed(self, url: str, *, client: httpx.AsyncClient | None = None) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False
        origin = f"{parsed.scheme}://{parsed.netloc}"
        entry = self._cache.get(origin)
        if entry is None:
            entry = await self._fetch(origin, client=client)
            self._cache[origin] = entry
        return entry.parser.can_fetch(self.user_agent, url)

    async def _fetch(self, origin: str, *, client: httpx.AsyncClient | None = None) -> RobotsEntry:
        robots_url = urljoin(origin, "/robots.txt")
        parser = urllib.robotparser.RobotFileParser(robots_url)
        owns_client = client is None
        http_client = client or httpx.AsyncClient(
            timeout=self.timeout_seconds, headers=default_headers()
        )
        try:
            response = await http_client.get(robots_url)
            if response.status_code in {401, 403}:
                parser.parse(["User-agent: *", "Disallow: /"])
                return RobotsEntry(parser=parser, fetched=True)
            if response.status_code >= 400:
                parser.parse(["User-agent: *", "Allow: /"])
                return RobotsEntry(parser=parser, fetched=False)
            parser.parse(response.text.splitlines())
            return RobotsEntry(parser=parser, fetched=True)
        except httpx.HTTPError:
            parser.parse(["User-agent: *", "Allow: /"])
            return RobotsEntry(parser=parser, fetched=False)
        finally:
            if owns_client:
                await http_client.aclose()
