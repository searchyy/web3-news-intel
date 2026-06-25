from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.event import EventRead

QMode = Literal["all", "any", "phrase"]
SortField = Literal[
    "published_at",
    "first_seen_at",
    "last_seen_at",
    "trust_score",
    "priority_score",
    "severity",
    "confirmation_count",
    "id",
]
SortDirection = Literal["asc", "desc"]


class EventSearchParams(BaseModel):
    q: str | None = None
    q_mode: QMode = "all"
    source_keys: list[str] = Field(default_factory=list)
    source_groups: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    severities: list[str] = Field(default_factory=list)
    priority_tiers: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    chains: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    official_only: bool | None = None
    minimum_trust_score: int | None = Field(default=None, ge=0, le=100)
    maximum_trust_score: int | None = Field(default=None, ge=0, le=100)
    minimum_priority_score: int | None = Field(default=None, ge=0, le=100)
    maximum_priority_score: int | None = Field(default=None, ge=0, le=100)
    minimum_ai_importance_score: int | None = Field(default=None, ge=0, le=100)
    has_ai_summary: bool | None = None
    published_from: datetime | None = None
    published_to: datetime | None = None
    first_seen_from: datetime | None = None
    first_seen_to: datetime | None = None
    sort: SortField = "first_seen_at"
    direction: SortDirection = "desc"
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=500)

    @field_validator(
        "source_keys",
        "source_groups",
        "categories",
        "severities",
        "priority_tiers",
        "statuses",
        "symbols",
        "chains",
        "languages",
        mode="before",
    )
    @classmethod
    def normalize_list(cls, value: Any) -> list[str]:
        if value is None or value == "":
            return []
        raw_values = value if isinstance(value, list) else [value]
        normalized: list[str] = []
        for raw in raw_values:
            for part in str(raw).split(","):
                item = part.strip()
                if item:
                    normalized.append(item)
        return normalized

    @field_validator("symbols", mode="after")
    @classmethod
    def uppercase_symbols(cls, value: list[str]) -> list[str]:
        return [item.upper() for item in value]

    @field_validator("priority_tiers", mode="after")
    @classmethod
    def normalize_priority_tiers(cls, value: list[str]) -> list[str]:
        return ["noise" if item.lower() == "noise" else item.upper() for item in value]

    @field_validator("q", mode="after")
    @classmethod
    def blank_q_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class EventSearchItem(EventRead):
    source_key: str | None = None
    source_name: str | None = None
    source_group: str | None = None
    official: bool | None = None
    ai_summary_status: str | None = None
    ai_headline_zh: str | None = None
    ai_summary_zh: str | None = None
    ai_importance_score: int | None = None
    ai_risk_level: str | None = None
    ai_tags: list[str] = Field(default_factory=list)
    has_ai_summary: bool = False


class EventSearchPage(BaseModel):
    items: list[EventSearchItem]
    total: int
    page: int
    page_size: int
    pages: int
    sort: SortField
    direction: SortDirection


class FacetBucket(BaseModel):
    key: str
    count: int
    label: str | None = None
    value: str | None = None

    @model_validator(mode="after")
    def hydrate_value(self) -> FacetBucket:
        if not self.value:
            self.value = self.key
        return self


class EventFacets(BaseModel):
    categories: list[FacetBucket] = Field(default_factory=list)
    severities: list[FacetBucket] = Field(default_factory=list)
    priority_tiers: list[FacetBucket] = Field(default_factory=list)
    statuses: list[FacetBucket] = Field(default_factory=list)
    languages: list[FacetBucket] = Field(default_factory=list)
    source_keys: list[FacetBucket] = Field(default_factory=list)
    source_groups: list[FacetBucket] = Field(default_factory=list)
    symbols: list[FacetBucket] = Field(default_factory=list)
    chains: list[FacetBucket] = Field(default_factory=list)


class SavedSearchCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    filters: EventSearchParams = Field(default_factory=EventSearchParams)


class SavedSearchPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    filters: EventSearchParams | None = None


class SavedSearchRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None = None
    filters: dict[str, Any]
    owner_subject: str
    created_at: datetime
    updated_at: datetime