from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    key: str
    name: str
    source_type: str
    adapter: str
    url: str
    canonical_url: str
    category: str
    language: str | None = None
    trust_score: int
    poll_seconds: int
    timeout_seconds: float
    max_response_bytes: int
    enabled: bool
    allow_private_networks: bool
    allow_localhost: bool
    config: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
