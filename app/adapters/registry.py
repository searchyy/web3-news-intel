from __future__ import annotations

from app.adapters.base import Adapter
from app.adapters.exchanges.http import ExchangeOfficialAdapter
from app.adapters.graphql import GraphQLAdapter
from app.adapters.html import HTMLAdapter
from app.adapters.json_api import JSONAPIAdapter
from app.adapters.media.html import MediaHTMLAdapter
from app.adapters.media.json_api import MediaJSONAPIAdapter
from app.adapters.media.rss import MediaRSSAdapter
from app.adapters.rss import RSSAdapter


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, Adapter] = {
            "rss": RSSAdapter(),
            "json_api": JSONAPIAdapter(),
            "graphql": GraphQLAdapter(),
            "html": HTMLAdapter(),
            "exchange_rss": ExchangeOfficialAdapter(),
            "exchange_json": ExchangeOfficialAdapter(),
            "exchange_html": ExchangeOfficialAdapter(),
            "okx_help_app_state": ExchangeOfficialAdapter(),
            "media_rss": MediaRSSAdapter(),
            "media_html": MediaHTMLAdapter(),
            "media_json_api": MediaJSONAPIAdapter(),
        }

    def get(self, name: str) -> Adapter:
        try:
            return self._adapters[name]
        except KeyError as exc:
            raise KeyError(f"unknown adapter {name!r}") from exc

    def names(self) -> list[str]:
        return sorted(self._adapters)


registry = AdapterRegistry()
