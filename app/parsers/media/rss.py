from __future__ import annotations

from typing import Any

import feedparser

from app.core.config import SourceConfig
from app.core.time import parse_datetime
from app.parsers.media.common import (
    SUMMARY_MAX_CHARS,
    classify_media_category,
    clean_text,
    list_from_value,
    media_source_group,
    safe_media_raw_metadata,
)
from app.pipeline.entities import extract_chains, extract_symbols
from app.pipeline.normalize import canonicalize_url
from app.schemas.normalized_item import NormalizedItem
from app.schemas.raw_document import RawDocumentPayload


def parse_media_rss(source: SourceConfig, raw: RawDocumentPayload) -> list[NormalizedItem]:
    feed = feedparser.parse(raw.body or "")
    max_items = int(source.config.get("max_items", source.config.get("max_items_per_fetch", 20)))
    items: list[NormalizedItem] = []
    for entry in feed.entries[: max_items * 3]:
        title = clean_text(entry.get("title"))
        if not title:
            continue
        summary = clean_text(
            entry.get("summary") or entry.get("description"),
            max_chars=int(source.config.get("summary_max_chars", SUMMARY_MAX_CHARS)),
        )
        tags = _entry_tags(entry)
        category, signals = classify_media_category(title, summary, tags, source.category)
        url = _entry_url(entry) or raw.url
        author = clean_text(entry.get("author"))
        published = parse_datetime(
            entry.get("published")
            or entry.get("updated")
            or entry.get("created")
            or entry.get("published_parsed")
            or entry.get("updated_parsed")
        )
        text = f"{title} {summary or ''}"
        items.append(
            NormalizedItem(
                title=title,
                summary=summary,
                url=url,
                canonical_url=canonicalize_url(url),
                published_at=published,
                source_key=source.key,
                source_type=source.source_type,
                category=category,
                language=source.language,
                symbols=extract_symbols(text),
                chains=extract_chains(text),
                raw=safe_media_raw_metadata(
                    source_key=source.key,
                    source_group=media_source_group(source.config, source.language),
                    parser="media_rss",
                    parser_version=str(source.config.get("parser_version", "media_rss_v1")),
                    provider_id=entry.get("id") or entry.get("guid"),
                    author=author,
                    tags=tags,
                    category=category,
                    category_signals=signals,
                    title=title,
                    summary=summary,
                    original_url=url,
                    official_confirmation=bool(source.config.get("official", False)),
                ),
            )
        )
        if len(items) >= max_items:
            break
    return items


def _entry_url(entry: Any) -> str | None:
    link = entry.get("link") if hasattr(entry, "get") else None
    if link:
        return str(link)
    for candidate in entry.get("links", []) if hasattr(entry, "get") else []:
        href = candidate.get("href")
        if href:
            return str(href)
    return None


def _entry_tags(entry: Any) -> list[str]:
    tags: list[str] = []
    for tag in entry.get("tags", []) if hasattr(entry, "get") else []:
        term = tag.get("term") or tag.get("label")
        tags.extend(list_from_value(term))
    return tags
