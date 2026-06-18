from __future__ import annotations

from urllib.parse import urljoin

from bs4 import BeautifulSoup

from app.core.config import SourceConfig
from app.core.time import parse_datetime
from app.fetch.client import FetchClient
from app.pipeline.normalize import canonicalize_url
from app.schemas.normalized_item import NormalizedItem
from app.schemas.raw_document import RawDocumentPayload


class HTMLAdapter:
    async def fetch(
        self, source: SourceConfig, fetch_client: FetchClient
    ) -> list[RawDocumentPayload]:
        response = await fetch_client.get_text(
            source.url,
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
                metadata={"adapter": "html", "parser": source.config.get("parser", "generic")},
            )
        ]

    async def parse(self, source: SourceConfig, raw: RawDocumentPayload) -> list[NormalizedItem]:
        soup = BeautifulSoup(raw.body or "", "lxml")
        config = source.config
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
