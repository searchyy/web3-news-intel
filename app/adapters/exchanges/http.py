from __future__ import annotations

from app.core.config import SourceConfig
from app.fetch.client import FetchClient
from app.fetch.conditionals import conditional_response_metadata, source_request_headers
from app.parsers.exchanges.html_parser import parse_html_announcements
from app.parsers.exchanges.json_parser import parse_json_announcements
from app.parsers.exchanges.rss_parser import parse_rss_announcements
from app.pipeline.normalize import canonicalize_url
from app.schemas.normalized_item import NormalizedItem
from app.schemas.raw_document import RawDocumentPayload

_ALLOWED_CONTENT_TYPES = (
    "application/json",
    "text/json",
    "application/rss+xml",
    "application/atom+xml",
    "application/xml",
    "text/xml",
    "text/html",
    "application/xhtml+xml",
    "text/plain",
)


class ExchangeOfficialAdapter:
    async def fetch(
        self,
        source: SourceConfig,
        fetch_client: FetchClient,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        canary: bool = False,
    ) -> list[RawDocumentPayload]:
        headers = source_request_headers(source, etag=etag, last_modified=last_modified)
        response = await fetch_client.get_text(
            source.url,
            headers=headers or None,
            respect_robots=bool(
                source.config.get("respect_robots", "html" in source.adapter)
            ),
            allowed_content_types=_ALLOWED_CONTENT_TYPES,
        )
        return [
            RawDocumentPayload(
                source_key=source.key,
                url=response.url,
                canonical_url=canonicalize_url(response.url),
                content_type=response.content_type,
                status_code=response.status_code,
                body_hash=response.body_hash,
                body=response.text,
                fetched_at=response.fetched_at,
                metadata={
                    "adapter": "exchange_official",
                    "canary": canary,
                    "max_canary_items": 10,
                    **conditional_response_metadata(response.headers),
                },
            )
        ]

    async def parse(
        self,
        source: SourceConfig,
        raw: RawDocumentPayload,
    ) -> list[NormalizedItem]:
        parser = str(source.config.get("parser") or source.config.get("format") or source.adapter)
        if "rss" in parser or source.adapter == "rss":
            return parse_rss_announcements(source, raw)
        if "html" in parser or "app_state" in parser or source.adapter in {
            "html",
            "okx_help_app_state",
        }:
            return parse_html_announcements(source, raw)
        return parse_json_announcements(source, raw)