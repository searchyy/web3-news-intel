from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.core.time import utc_now


class RawDocumentPayload(BaseModel):
    source_key: str
    url: str
    canonical_url: str | None = None
    content_type: str | None = None
    status_code: int | None = None
    body_hash: str
    body: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    fetched_at: datetime = Field(default_factory=utc_now)
