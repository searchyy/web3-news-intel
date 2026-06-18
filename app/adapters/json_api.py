from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from app.core.config import SourceConfig
from app.core.time import parse_datetime
from app.fetch.client import FetchClient
from app.pipeline.normalize import canonicalize_url
from app.schemas.normalized_item import NormalizedItem
from app.schemas.raw_document import RawDocumentPayload


class JSONAPIAdapter:
    async def fetch(
        self, source: SourceConfig, fetch_client: FetchClient
    ) -> list[RawDocumentPayload]:
        response = await fetch_client.get_text(
            source.url,
            allowed_content_types=("application/json", "text/json", "text/plain"),
        )
        return [
            RawDocumentPayload(
                source_key=source.key,
                url=source.url,
                canonical_url=canonicalize_url(source.url),
                content_type=response.content_type,
                status_code=response.status_code,
                body_hash=response.body_hash,
                body=response.text,
                fetched_at=response.fetched_at,
                metadata={"adapter": "json_api"},
            )
        ]

    async def parse(self, source: SourceConfig, raw: RawDocumentPayload) -> list[NormalizedItem]:
        data = json.loads(raw.body or "{}")
        entries = _items_from_data(data, source.config)
        items: list[NormalizedItem] = []
        for entry in entries:
            title = _first(entry, source.config.get("title_fields", ["title", "name", "headline"]))
            if not title:
                continue
            url = _first(entry, source.config.get("url_fields", ["url", "link", "href"])) or raw.url
            summary = _first(
                entry, source.config.get("summary_fields", ["summary", "description", "details"])
            )
            published = parse_datetime(
                _first(
                    entry, source.config.get("date_fields", ["published_at", "date", "timestamp"])
                )
            )
            items.append(
                NormalizedItem(
                    title=str(title),
                    summary=str(summary)[:1000] if summary else None,
                    url=str(url),
                    canonical_url=canonicalize_url(str(url)),
                    published_at=published,
                    source_key=source.key,
                    source_type=source.source_type,
                    category=source.category,
                    language=source.language,
                    raw={
                        **_json_safe(entry),
                        "parser_version": source.config.get("parser_version", "generic_json_v1"),
                    },
                )
            )
        return items


def _items_from_data(data: Any, config: dict[str, Any]) -> Iterable[dict[str, Any]]:
    path = config.get("items_path")
    if path:
        data = _resolve_path(data, str(path))
    elif isinstance(data, dict):
        for key in ("items", "results", "data", "hacks", "proposals"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def _resolve_path(data: Any, path: str) -> Any:
    current = data
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _first(entry: dict[str, Any], fields: list[str]) -> Any:
    for field in fields:
        if entry.get(field) not in (None, ""):
            return entry[field]
    return None


def _json_safe(entry: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(entry, default=str))
