from __future__ import annotations

from urllib.parse import urljoin

from bs4 import BeautifulSoup

from app.core.config import SourceConfig
from app.core.time import parse_datetime
from app.parsers.media.common import (
    SUMMARY_MAX_CHARS,
    classify_media_category,
    clean_text,
    media_source_group,
    safe_media_raw_metadata,
)
from app.pipeline.entities import extract_chains, extract_symbols
from app.pipeline.normalize import canonicalize_url
from app.schemas.normalized_item import NormalizedItem
from app.schemas.raw_document import RawDocumentPayload


def parse_media_html(source: SourceConfig, raw: RawDocumentPayload) -> list[NormalizedItem]:
    soup = BeautifulSoup(raw.body or "", "lxml")
    config = source.config
    max_items = int(config.get("max_items", config.get("max_items_per_fetch", 20)))
    item_selector = str(config.get("item_selector", "article, a[href]"))
    items: list[NormalizedItem] = []
    for node in soup.select(item_selector)[: max_items * 3]:
        title_node = node.select_one(str(config.get("title_selector", ""))) if config.get(
            "title_selector"
        ) else node
        href_node = node.select_one(str(config.get("url_selector", ""))) if config.get(
            "url_selector"
        ) else node
        if title_node is None or href_node is None:
            continue
        title = clean_text(title_node.get_text(" ", strip=True))
        href = href_node.get("href") if hasattr(href_node, "get") else None
        if not title or not href:
            continue
        url = urljoin(raw.url, str(href))
        summary = _optional_text(node, str(config.get("summary_selector", "")))
        if summary:
            summary = clean_text(
                summary,
                max_chars=int(config.get("summary_max_chars", SUMMARY_MAX_CHARS)),
            )
        author = clean_text(_optional_text(node, str(config.get("author_selector", ""))))
        tag_selector = str(config.get("tag_selector", ""))
        tags = []
        if tag_selector:
            tags = [
                clean
                for candidate in node.select(tag_selector)
                if (clean := clean_text(candidate.get_text(" ", strip=True)))
            ]
        published_text = _optional_text(node, str(config.get("date_selector", "")))
        published = parse_datetime(published_text)
        category, signals = classify_media_category(title, summary, tags, source.category)
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
                    source_group=media_source_group(config, source.language),
                    parser="media_html",
                    parser_version=str(config.get("parser_version", "media_html_v1")),
                    provider_id=node.get(str(config.get("id_attribute", "data-id"))),
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


def _optional_text(node: object, selector: str) -> str | None:
    if not selector or not hasattr(node, "select_one"):
        return None
    selected = node.select_one(selector)
    if selected is None:
        return None
    return selected.get_text(" ", strip=True)
