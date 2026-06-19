from __future__ import annotations

import html
import re

import feedparser

from app.core.config import SourceConfig
from app.core.time import parse_datetime
from app.fetch.client import FetchClient
from app.pipeline.normalize import canonicalize_url
from app.schemas.normalized_item import NormalizedItem
from app.schemas.raw_document import RawDocumentPayload

TAG_RE = re.compile(r"<[^>]+>")


class RSSAdapter:
    async def fetch(
        self, source: SourceConfig, fetch_client: FetchClient
    ) -> list[RawDocumentPayload]:
        response = await fetch_client.get_text(
            source.url,
            allowed_content_types=(
                "application/rss+xml",
                "application/atom+xml",
                "application/xml",
                "text/xml",
                "text/plain",
            ),
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
                    "adapter": "rss",
                    "final_url": response.url,
                    "response_bytes": len(response.text.encode("utf-8")),
                },
            )
        ]

    async def parse(self, source: SourceConfig, raw: RawDocumentPayload) -> list[NormalizedItem]:
        feed = feedparser.parse(raw.body or "")
        items: list[NormalizedItem] = []
        for entry in feed.entries:
            url = _entry_url(entry) or raw.url
            title = html.unescape(str(entry.get("title") or "")).strip()
            if not title:
                continue
            summary = _clean_summary(entry.get("summary") or entry.get("description"))
            published = parse_datetime(
                entry.get("published")
                or entry.get("updated")
                or entry.get("created")
                or entry.get("published_parsed")
                or entry.get("updated_parsed")
            )
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
                        "id": entry.get("id"),
                        "tags": [tag.get("term") for tag in entry.get("tags", [])],
                        "parser_version": source.config.get("parser_version", "generic_rss_v1"),
                    },
                )
            )
        return items


def _entry_url(entry: object) -> str | None:
    link = entry.get("link") if hasattr(entry, "get") else None
    if link:
        return str(link)
    for candidate in entry.get("links", []) if hasattr(entry, "get") else []:
        href = candidate.get("href")
        if href:
            return str(href)
    return None


def _clean_summary(value: object) -> str | None:
    if not value:
        return None
    text = TAG_RE.sub(" ", html.unescape(str(value)))
    text = " ".join(text.split())
    return text[:1000] if text else None
