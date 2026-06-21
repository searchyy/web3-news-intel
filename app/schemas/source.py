from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    key: str
    name: str
    display_name_zh: str | None = None
    source_group: str = "legacy"
    source_type: str
    adapter: str
    url: str
    canonical_url: str
    category: str
    language: str | None = None
    official: bool = False
    trust_score: int
    poll_seconds: int
    timeout_seconds: float
    max_response_bytes: int
    maximum_response_bytes: int = 0
    max_items_per_fetch: int = 50
    enabled: bool
    allow_private_networks: bool
    allow_localhost: bool
    ranking_provider: str | None = None
    ranking_position: int | None = None
    ranking_snapshot_at: datetime | None = None
    parser_version: str = "v1"
    supported_categories: list[str] = Field(default_factory=list)
    health_status: str = "unknown"
    live_canary_status: str = "unknown"
    last_canary_at: datetime | None = None
    last_canary_error: str | None = None
    last_fetch_at: datetime | None = None
    last_success_at: datetime | None = None
    last_parsed_count: int = 0
    last_http_status: int | None = None
    last_error: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    cursor: str | None = None
    consecutive_failures: int = 0
    circuit_open_until: datetime | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def hydrate_alias_fields(self) -> SourceRead:
        self.maximum_response_bytes = self.max_response_bytes
        return self
