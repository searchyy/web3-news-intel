from __future__ import annotations

from datetime import datetime
from typing import Any, Self

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from app.core.i18n import category_label, severity_label, status_label


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
    priority_score: int = 0
    priority_tier: str = "noise"
    source_count: int = 1
    score_reasons: list[str] = Field(default_factory=list)
    noise_reasons: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    chains: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(
        default_factory=dict, validation_alias=AliasChoices("metadata_", "metadata")
    )
    display_title: str = ""
    display_summary: str | None = None
    category_label: str = ""
    severity_label: str = ""
    status_label: str = ""

    @model_validator(mode="after")
    def hydrate_display_fields(self) -> Self:
        metadata = self.metadata or {}
        cluster = metadata.get("cluster") if isinstance(metadata.get("cluster"), dict) else {}
        self.priority_score = int(metadata.get("priority_score") or self.priority_score or 0)
        self.priority_tier = str(metadata.get("priority_tier") or self.priority_tier or "noise")
        self.source_count = int(cluster.get("source_count") or self.confirmation_count or 1)
        self.score_reasons = [str(item) for item in metadata.get("score_reasons") or []]
        self.noise_reasons = [str(item) for item in metadata.get("noise_reasons") or []]
        self.display_title = self.title
        self.display_summary = self.summary
        self.category_label = category_label(self.category) or self.category
        self.severity_label = severity_label(self.severity) or self.severity
        self.status_label = status_label(self.status) or self.status
        return self


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
