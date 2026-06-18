from __future__ import annotations

from app.adapters.base import Adapter
from app.adapters.graphql import GraphQLAdapter
from app.adapters.html import HTMLAdapter
from app.adapters.json_api import JSONAPIAdapter
from app.adapters.rss import RSSAdapter


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, Adapter] = {
            "rss": RSSAdapter(),
            "json_api": JSONAPIAdapter(),
            "graphql": GraphQLAdapter(),
            "html": HTMLAdapter(),
        }

    def get(self, name: str) -> Adapter:
        try:
            return self._adapters[name]
        except KeyError as exc:
            raise KeyError(f"unknown adapter {name!r}") from exc

    def names(self) -> list[str]:
        return sorted(self._adapters)


registry = AdapterRegistry()
