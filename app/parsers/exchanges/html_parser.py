from __future__ import annotations

import json
import re
from typing import Any

from bs4 import BeautifulSoup

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


def parse_html_announcements(
    source: SourceConfig,
    raw: RawDocumentPayload,
) -> list[NormalizedItem]:
    soup = BeautifulSoup(raw.body or "", "lxml")
    if source.config.get("app_state_selector") or source.config.get("parser") in {
        "okx_help_app_state",
        "exchange_html_app_state",
    }:
        return _parse_app_state(source, raw, soup)
    return _parse_css_listing(source, raw, soup)


def _parse_css_listing(
    source: SourceConfig,
    raw: RawDocumentPayload,
    soup: BeautifulSoup,
) -> list[NormalizedItem]:
    selector = str(source.config.get("item_selector", "a[href]"))
    title_selector = source.config.get("title_selector")
    url_selector = source.config.get("url_selector")
    summary_selector = source.config.get("summary_selector")
    date_selector = source.config.get("date_selector")
    parser_version = str(source.config.get("parser_version", "exchange_html_v1"))
    limit = max_items_for_parse(source, raw)
    items: list[NormalizedItem] = []
    for node in soup.select(selector):
        title_node = node.select_one(str(title_selector)) if title_selector else node
        url_node = node.select_one(str(url_selector)) if url_selector else node
        summary_node = node.select_one(str(summary_selector)) if summary_selector else None
        date_node = node.select_one(str(date_selector)) if date_selector else None
        published_text = date_node.get_text(" ", strip=True) if date_node else None
        if not published_text:
            published_text = _regex_date_text(
                node.get_text(" ", strip=True),
                source.config.get("date_regex"),
            )
        href = url_node.get("href") if hasattr(url_node, "get") and url_node else None
        item = build_normalized_item(
            source=source,
            raw=raw,
            title=title_node.get_text(" ", strip=True) if title_node else None,
            url=href,
            summary=summary_node.get_text(" ", strip=True) if summary_node else None,
            published_at=parse_exchange_datetime(published_text),
            parser_name=str(source.config.get("parser", "exchange_html")),
            parser_version=parser_version,
        )
        if item is not None:
            items.append(item)
        if len(items) >= limit:
            break
    return dedupe_items(items)


def _regex_date_text(text: str, pattern: object) -> str | None:
    if not pattern:
        return None
    try:
        match = re.search(str(pattern), text)
    except re.error:
        return None
    if match is None:
        return None
    if "date" in match.groupdict():
        return match.group("date")
    if match.groups():
        return match.group(1)
    return match.group(0)


def _parse_app_state(
    source: SourceConfig,
    raw: RawDocumentPayload,
    soup: BeautifulSoup,
) -> list[NormalizedItem]:
    selector = str(
        source.config.get("app_state_selector", 'script#appState[type="application/json"]')
    )
    script = soup.select_one(selector)
    if script is None:
        return []
    try:
        payload = json.loads(script.string or script.get_text() or "{}")
    except json.JSONDecodeError:
        return []
    entries = resolve_path(payload, source.config.get("items_path"))
    if not isinstance(entries, list):
        return []
    parser_version = str(source.config.get("parser_version", "exchange_html_app_state_v1"))
    limit = max_items_for_parse(source, raw)
    items: list[NormalizedItem] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        title = first_value(entry, source.config.get("title_fields", ["title", "name"]))
        url = first_value(entry, source.config.get("url_fields", ["url", "href", "slug"]))
        if source.config.get("url_template"):
            url = render_template(source.config.get("url_template"), entry) or url
        published_at = parse_exchange_datetime(
            first_value(
                entry,
                source.config.get("date_fields", ["publishTime", "publishedAt", "date"]),
            ),
            unit=source.config.get("timestamp_unit") or source.config.get("date_unit"),
        )
        item = build_normalized_item(
            source=source,
            raw=raw,
            title=title,
            url=url,
            summary=first_value(entry, source.config.get("summary_fields", ["summary", "desc"])),
            published_at=published_at,
            item_id=first_value(entry, source.config.get("id_fields", ["id", "code", "slug"])),
            parser_name=str(source.config.get("parser", "exchange_html_app_state")),
            parser_version=parser_version,
            extra_raw={"raw_category": _raw_category(entry)},
        )
        if item is not None:
            items.append(item)
        if len(items) >= limit:
            break
    return dedupe_items(items)


def _raw_category(entry: dict[str, Any]) -> Any:
    return entry.get("category") or entry.get("categorySlug") or entry.get("sectionSlug")
