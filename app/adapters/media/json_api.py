from __future__ import annotations

from app.core.config import SourceConfig
from app.fetch.client import FetchClient
from app.parsers.media.json_api import parse_media_json
from app.pipeline.normalize import canonicalize_url
from app.schemas.normalized_item import NormalizedItem
from app.schemas.raw_document import RawDocumentPayload


class MediaJSONAPIAdapter:
    async def fetch(
        self, source: SourceConfig, fetch_client: FetchClient
    ) -> list[RawDocumentPayload]:
        response = await fetch_client.get_text(
            source.url,
            allowed_content_types=("application/json", "text/json", "text/plain"),
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
                metadata={"adapter": "media_json_api", "copyright_scope": "metadata_only"},
            )
        ]

    async def parse(self, source: SourceConfig, raw: RawDocumentPayload) -> list[NormalizedItem]:
        return parse_media_json(source, raw)
