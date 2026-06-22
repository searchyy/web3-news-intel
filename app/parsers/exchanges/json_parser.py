from __future__ import annotations

import json
from typing import Any

from app.core.config import SourceConfig
from app.parsers.exchanges.common import (
    build_normalized_item,
    dedupe_items,
    first_value,
    max_items_for_parse,
    parse_exchange_datetime,
    render_template,
    resolve_path,
)
from app.schemas.normalized_item import NormalizedItem
from app.schemas.raw_document import RawDocumentPayload

DEFAULT_TITLE_FIELDS = ("title", "name", "headline", "articleTitle")
DEFAULT_SUMMARY_FIELDS = ("summary", "description", "desc", "body", "content")
DEFAULT_DATE_FIELDS = ("published_at", "publishTime", "releaseDate", "date", "timestamp")
DEFAULT_URL_FIELDS = ("url", "link", "href", "articleUrl")
DEFAULT_ID_FIELDS = ("id", "code", "slug", "articleId")


def parse_json_announcements(
    source: SourceConfig,
    raw: RawDocumentPayload,
) -> list[NormalizedItem]:
    try:
        payload = json.loads(raw.body or "{}")
    except json.JSONDecodeError:
        return []

    entries = _items_from_payload(payload, source.config)
    limit = max_items_for_parse(source, raw)
    parser_version = str(source.config.get("parser_version", "exchange_json_v1"))
    items: list[NormalizedItem] = []
    for entry in entries:
        title = first_value(entry, source.config.get("title_fields", list(DEFAULT_TITLE_FIELDS)))
        url = first_value(entry, source.config.get("url_fields", list(DEFAULT_URL_FIELDS)))
        url = url or render_template(source.config.get("url_template"), entry)
        published_at = parse_exchange_datetime(
            first_value(entry, source.config.get("date_fields", list(DEFAULT_DATE_FIELDS))),
            unit=source.config.get("timestamp_unit") or source.config.get("date_unit"),
        )
        item = build_normalized_item(
            source=source,
            raw=raw,
            title=title,
            url=url,
            summary=first_value(
                entry,
                source.config.get("summary_fields", list(DEFAULT_SUMMARY_FIELDS)),
            ),
            published_at=published_at,
            item_id=first_value(entry, source.config.get("id_fields", list(DEFAULT_ID_FIELDS))),
            parser_name=str(source.config.get("parser", "exchange_json")),
            parser_version=parser_version,
            extra_raw={
                "raw_category": first_value(
                    entry,
                    source.config.get("category_fields", ["category"]),
                ),
            },
        )
        if item is not None:
            items.append(item)
        if len(items) >= limit:
            break
    return dedupe_items(items)


def _items_from_payload(payload: Any, config: dict[str, Any]) -> list[dict[str, Any]]:
    data = resolve_path(payload, config.get("items_path"))
    if data is None and isinstance(payload, dict):
        for key in ("items", "results", "data", "articles", "list", "rows"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                data = candidate
                break
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []
