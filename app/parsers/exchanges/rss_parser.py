from __future__ import annotations

import feedparser

from app.core.config import SourceConfig
from app.parsers.exchanges.common import (
    build_normalized_item,
    dedupe_items,
    first_value,
    max_items_for_parse,
    parse_exchange_datetime,
)
from app.schemas.normalized_item import NormalizedItem
from app.schemas.raw_document import RawDocumentPayload


def parse_rss_announcements(
    source: SourceConfig,
    raw: RawDocumentPayload,
) -> list[NormalizedItem]:
    feed = feedparser.parse(raw.body or "")
    parser_version = str(source.config.get("parser_version", "exchange_rss_v1"))
    limit = max_items_for_parse(source, raw)
    items: list[NormalizedItem] = []
    for entry in feed.entries:
        entry_dict = dict(entry)
        item = build_normalized_item(
            source=source,
            raw=raw,
            title=first_value(entry_dict, source.config.get("title_fields", ["title"])),
            url=first_value(entry_dict, source.config.get("url_fields", ["link", "id"])),
            summary=first_value(
                entry_dict,
                source.config.get("summary_fields", ["summary", "description"]),
            ),
            published_at=parse_exchange_datetime(
                first_value(
                    entry_dict,
                    source.config.get(
                        "date_fields",
                        ["published", "updated", "created", "published_parsed", "updated_parsed"],
                    ),
                )
            ),
            item_id=first_value(entry_dict, source.config.get("id_fields", ["id", "guid", "link"])),
            parser_name=str(source.config.get("parser", "exchange_rss")),
            parser_version=parser_version,
            extra_raw={
                "tags": [
                    tag.get("term")
                    for tag in entry_dict.get("tags", [])
                    if isinstance(tag, dict) and tag.get("term")
                ],
            },
        )
        if item is not None:
            items.append(item)
        if len(items) >= limit:
            break
    return dedupe_items(items)
