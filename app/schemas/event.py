from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class EventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_key: str
    title: str
    summary: str | None = None
    category: str
    status: str
    severity: str
    language: str | None = None
    primary_url: str | None = None
    published_at: datetime | None = None
    first_seen_at: datetime
    last_seen_at: datetime
    trust_score: int
    confirmation_count: int
    symbols: list[str] = Field(default_factory=list)
    chains: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(
        default_factory=dict, validation_alias=AliasChoices("metadata_", "metadata")
    )


class EventSourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_id: int
    raw_document_id: int | None = None
    url: str
    title: str | None = None
    published_at: datetime | None = None
    source_score: int
    created_at: datetime


class EventDetail(EventRead):
    sources: list[EventSourceRead] = Field(default_factory=list)
