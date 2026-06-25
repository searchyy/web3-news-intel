from __future__ import annotations

from app.core.config import SourceConfig
from app.fetch.client import FetchClient
from app.fetch.conditionals import conditional_response_metadata, source_request_headers
from app.parsers.media.html import parse_media_html
from app.pipeline.normalize import canonicalize_url
from app.schemas.normalized_item import NormalizedItem
from app.schemas.raw_document import RawDocumentPayload


class MediaHTMLAdapter:
    async def fetch(
        self,
        source: SourceConfig,
        fetch_client: FetchClient,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> list[RawDocumentPayload]:
        headers = source_request_headers(source, etag=etag, last_modified=last_modified)
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
                    "adapter": "media_html",
                    "copyright_scope": "listing_metadata_only",
                    **conditional_response_metadata(response.headers),
                },
            )
        ]

    async def parse(self, source: SourceConfig, raw: RawDocumentPayload) -> list[NormalizedItem]:
        return parse_media_html(source, raw)
