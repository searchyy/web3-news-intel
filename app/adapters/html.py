from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from app.core.config import SourceConfig
from app.core.time import parse_datetime
from app.fetch.client import FetchClient
from app.fetch.conditionals import conditional_request_headers, conditional_response_metadata
from app.pipeline.normalize import canonicalize_url
from app.schemas.normalized_item import NormalizedItem
from app.schemas.raw_document import RawDocumentPayload


class HTMLAdapter:
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
            respect_robots=True,
            allowed_content_types=("text/html", "application/xhtml+xml", "text/plain"),
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
                metadata={
                    "adapter": "html",
                    "parser": source.config.get("parser", "generic"),
                    **conditional_response_metadata(response.headers),
                },
            )
        ]

    async def parse(self, source: SourceConfig, raw: RawDocumentPayload) -> list[NormalizedItem]:
        soup = BeautifulSoup(raw.body or "", "lxml")
        config = source.config
        if config.get("parser") == "okx_help_app_state":
            return _parse_okx_help_app_state(source, raw, soup)
        max_items = int(config.get("max_items", 20))
        item_selector = config.get("item_selector")
        nodes = soup.select(item_selector) if item_selector else soup.select("a[href]")
        items: list[NormalizedItem] = []
        for node in nodes[: max_items * 3]:
            title_node = (
                node.select_one(config["title_selector"]) if config.get("title_selector") else node
            )
            href_node = (
                node.select_one(config["url_selector"]) if config.get("url_selector") else node
            )
            if title_node is None or href_node is None:
                continue
            title = " ".join(title_node.get_text(" ", strip=True).split())
            href = href_node.get("href") if hasattr(href_node, "get") else None
            if not title or not href:
                continue
            url = urljoin(raw.url, str(href))
            summary_node = (
                node.select_one(config["summary_selector"])
                if config.get("summary_selector")
                else None
            )
            date_node = (
                node.select_one(config["date_selector"]) if config.get("date_selector") else None
            )
            summary = (
                " ".join(summary_node.get_text(" ", strip=True).split())[:1000]
                if summary_node is not None
                else None
            )
            published = parse_datetime(date_node.get_text(" ", strip=True)) if date_node else None
            items.append(
                NormalizedItem(
                    title=title,
                    summary=summary,
                    url=url,
                    canonical_url=canonicalize_url(url),
                    published_at=published,
                    source_key=source.key,
                    source_type=source.source_type,
                    category=source.category,
                    language=source.language,
                    raw={
                        "parser": config.get("parser", "generic"),
                        "parser_version": config.get("parser_version", "generic_html_v1"),
                    },
                )
            )
            if len(items) >= max_items:
                break
        return items


def _parse_okx_help_app_state(
    source: SourceConfig,
    raw: RawDocumentPayload,
    soup: BeautifulSoup,
) -> list[NormalizedItem]:
    script = soup.select_one('script#appState[type="application/json"]')
    if script is None or not script.string:
        return []
    try:
        payload = json.loads(script.string)
    except json.JSONDecodeError:
        return []
    entries = _resolve_path(
        payload,
        source.config.get(
            "items_path",
            "appContext.initialProps.sectionData.articleList.list",
        ),
    )
    if not isinstance(entries, list):
        return []
    max_items = int(source.config.get("max_items", 20))
    url_template = str(source.config.get("url_template", "https://www.okx.com/help/{slug}"))
    items: list[NormalizedItem] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "").strip()
        slug = str(entry.get("slug") or entry.get("id") or "").strip()
        if not title or not slug:
            continue
        url = _format_url(url_template, entry, raw.url)
        published = _milliseconds_to_datetime(entry.get("publishTime"))
        items.append(
            NormalizedItem(
                title=title,
                summary=None,
                url=url,
                canonical_url=canonicalize_url(url),
                published_at=published,
                source_key=source.key,
                source_type=source.source_type,
                category=source.category,
                language=source.language,
                raw={
                    "id": entry.get("id"),
                    "slug": slug,
                    "sectionSlug": entry.get("sectionSlug"),
                    "categorySlug": entry.get("categorySlug"),
                    "parser": source.config.get("parser", "okx_help_app_state"),
                    "parser_version": source.config.get(
                        "parser_version", "okx_help_app_state_v1"
                    ),
                },
            )
        )
        if len(items) >= max_items:
            break
    return items


def _resolve_path(data: Any, path: str) -> Any:
    current = data
    for part in str(path).split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if 0 <= index < len(current) else None
        else:
            return None
    return current


def _format_url(template: str, entry: dict[str, Any], fallback: str) -> str:
    try:
        return template.format(**entry)
    except (KeyError, ValueError):
        return fallback


def _milliseconds_to_datetime(value: Any) -> datetime | None:
    if not isinstance(value, int | float):
        return parse_datetime(value)
    try:
        return datetime.fromtimestamp(float(value) / 1000, tz=UTC)
    except (OSError, OverflowError, ValueError):
        return None
