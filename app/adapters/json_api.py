from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from app.core.config import SourceConfig
from app.core.time import parse_datetime
from app.fetch.client import FetchClient
from app.fetch.conditionals import conditional_request_headers, conditional_response_metadata
from app.pipeline.normalize import canonicalize_url
from app.schemas.normalized_item import NormalizedItem
from app.schemas.raw_document import RawDocumentPayload


class JSONAPIAdapter:
    async def fetch(
        self,
        source: SourceConfig,
        fetch_client: FetchClient,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> list[RawDocumentPayload]:
        headers = conditional_request_headers(etag=etag, last_modified=last_modified)
        response = await fetch_client.get_text(
            source.url,
            headers=headers or None,
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
                metadata={"adapter": "json_api", **conditional_response_metadata(response.headers)},
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
            url = _entry_url(entry, source.config, raw.url)
            summary = _first(
                entry, source.config.get("summary_fields", ["summary", "description", "details"])
            )
            published = _entry_datetime(entry, source.config)
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
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if 0 <= index < len(current) else None
        else:
            return None
    return current


def _first(entry: dict[str, Any], fields: list[str]) -> Any:
    for field in fields:
        if entry.get(field) not in (None, ""):
            return entry[field]
    return None


def _entry_url(entry: dict[str, Any], config: dict[str, Any], fallback: str) -> str:
    direct = _first(entry, config.get("url_fields", ["url", "link", "href"]))
    if direct:
        return str(direct)
    template = config.get("url_template")
    if template:
        try:
            return str(template).format(**entry)
        except (KeyError, ValueError):
            return fallback
    return fallback


def _entry_datetime(entry: dict[str, Any], config: dict[str, Any]) -> datetime | None:
    raw_value = _first(entry, config.get("date_fields", ["published_at", "date", "timestamp"]))
    if raw_value in (None, ""):
        return None
    if config.get("timestamp_unit") == "milliseconds" and isinstance(raw_value, int | float):
        try:
            return datetime.fromtimestamp(float(raw_value) / 1000, tz=UTC)
        except (OSError, OverflowError, ValueError):
            return None
    return parse_datetime(raw_value)


def _json_safe(entry: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(entry, default=str))
