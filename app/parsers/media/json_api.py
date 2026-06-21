from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any
from urllib.parse import urljoin

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


def parse_media_json(source: SourceConfig, raw: RawDocumentPayload) -> list[NormalizedItem]:
    try:
        data = json.loads(raw.body or "{}")
    except json.JSONDecodeError:
        return []
    entries = _items_from_data(data, source.config)
    max_items = int(source.config.get("max_items", source.config.get("max_items_per_fetch", 20)))
    items: list[NormalizedItem] = []
    for entry in entries:
        title = clean_text(_first(entry, source.config.get("title_fields", ["title", "headline"])))
        if not title:
            continue
        summary_fields = source.config.get("summary_fields", ["summary", "description", "excerpt"])
        summary = clean_text(
            _first(entry, summary_fields),
            max_chars=int(source.config.get("summary_max_chars", SUMMARY_MAX_CHARS)),
        )
        tags = list_from_value(_first(entry, source.config.get("tag_fields", ["tags", "category"])))
        category, signals = classify_media_category(title, summary, tags, source.category)
        url = _entry_url(entry, source.config, raw.url)
        author = clean_text(_first(entry, source.config.get("author_fields", ["author", "byline"])))
        date_fields = source.config.get("date_fields", ["published_at", "publishedAt", "date"])
        published = parse_datetime(_first(entry, date_fields))
        id_fields = source.config.get("id_fields", ["id", "guid", "uuid"])
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
                    parser="media_json_api",
                    parser_version=str(source.config.get("parser_version", "media_json_api_v1")),
                    provider_id=_first(entry, id_fields),
                    author=author,
                    tags=tags,
                    category=category,
                    category_signals=signals,
                    title=title,
                    summary=summary,
                    original_url=url,
                ),
            )
        )
        if len(items) >= max_items:
            break
    return items


def _items_from_data(data: Any, config: dict[str, Any]) -> Iterable[dict[str, Any]]:
    path = config.get("items_path")
    if path:
        data = _resolve_path(data, str(path))
    elif isinstance(data, dict):
        for key in ("items", "results", "data", "articles", "newsflashes"):
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
        return urljoin(fallback, str(direct))
    template = config.get("url_template")
    if template:
        try:
            return str(template).format(**entry)
        except (KeyError, ValueError):
            return fallback
    return fallback
