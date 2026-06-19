from __future__ import annotations

import json
from typing import Any

from app.core.config import SourceConfig
from app.core.time import parse_datetime
from app.fetch.client import FetchClient
from app.pipeline.normalize import canonicalize_url
from app.schemas.normalized_item import NormalizedItem
from app.schemas.raw_document import RawDocumentPayload

DEFAULT_SNAPSHOT_QUERY = """
query RecentProposals($spaces: [String!]!) {
  proposals(
    first: 20,
    skip: 0,
    where: { space_in: $spaces },
    orderBy: "created",
    orderDirection: desc
  ) {
    id
    title
    body
    state
    created
    link
    space { id name }
  }
}
"""


class GraphQLAdapter:
    async def fetch(
        self, source: SourceConfig, fetch_client: FetchClient
    ) -> list[RawDocumentPayload]:
        payload = {
            "query": source.config.get("query") or DEFAULT_SNAPSHOT_QUERY,
            "variables": source.config.get("variables")
            or {"spaces": source.config.get("spaces", [])},
        }
        response = await fetch_client.post_json(
            source.url,
            json=payload,
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
                metadata={
                    "adapter": "graphql",
                    "query_name": source.config.get("query_name"),
                    "final_url": response.url,
                    "response_bytes": len(response.text.encode("utf-8")),
                },
            )
        ]

    async def parse(self, source: SourceConfig, raw: RawDocumentPayload) -> list[NormalizedItem]:
        data = json.loads(raw.body or "{}")
        entries = _extract_entries(data, source.config.get("items_path", "data.proposals"))
        items: list[NormalizedItem] = []
        for entry in entries:
            title = entry.get("title")
            if not title:
                continue
            url = entry.get("link") or f"{raw.url}#{entry.get('id')}"
            created = entry.get("created")
            items.append(
                NormalizedItem(
                    title=str(title),
                    summary=str(entry.get("body") or "")[:1000] or None,
                    url=str(url),
                    canonical_url=canonicalize_url(str(url)),
                    published_at=parse_datetime(created),
                    source_key=source.key,
                    source_type=source.source_type,
                    category=source.category,
                    language=source.language,
                    raw={
                        **entry,
                        "parser_version": source.config.get("parser_version", "generic_graphql_v1"),
                    },
                )
            )
        return items


def _extract_entries(data: Any, path: str) -> list[dict[str, Any]]:
    current = data
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return []
    if isinstance(current, list):
        return [entry for entry in current if isinstance(entry, dict)]
    return []
