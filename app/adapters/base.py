from __future__ import annotations

from typing import Protocol

from app.core.config import SourceConfig
from app.fetch.client import FetchClient
from app.schemas.normalized_item import NormalizedItem
from app.schemas.raw_document import RawDocumentPayload


class Adapter(Protocol):
    async def fetch(
        self,
        source: SourceConfig,
        fetch_client: FetchClient,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> list[RawDocumentPayload]: ...

    async def parse(
        self, source: SourceConfig, raw: RawDocumentPayload
    ) -> list[NormalizedItem]: ...
