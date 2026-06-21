from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.event import EventRead

QueryMode = Literal["all", "any", "phrase"]
SortField = Literal["published_at", "first_seen_at", "severity", "trust_score", "id"]
SortDirection = Literal["asc", "desc"]


class EventSearchParams(BaseModel):
    q: str | None = None
    q_mode: QueryMode = "all"
    source_keys: list[str] = Field(default_factory=list)
    source_groups: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    severities: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    chains: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    official_only: bool = False
    minimum_trust_score: int | None = Field(default=None, ge=0, le=100)
    has_ai_summary: bool | None = None
    published_from: datetime | None = None
    published_to: datetime | None = None
    first_seen_from: datetime | None = None
    first_seen_to: datetime | None = None
    sort: SortField = "published_at"
    direction: SortDirection = "desc"
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)

    @field_validator(
        "source_keys",
        "source_groups",
        "categories",
        "severities",
        "statuses",
        "symbols",
        "chains",
        "languages",
        mode="before",
    )
    @classmethod
    def split_csv(cls, value: Any) -> list[str]:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, list):
            items: list[str] = []
            for item in value:
                if isinstance(item, str) and "," in item:
                    items.extend(part.strip() for part in item.split(",") if part.strip())
                elif item is not None and str(item).strip():
                    items.append(str(item).strip())
            return items
        return [str(value).strip()]


class EventSearchResponse(BaseModel):
    items: list[EventRead]
    total: int
    page: int
    page_size: int
    pages: int


class EventFacets(BaseModel):
    source_keys: list[dict[str, Any]] = Field(default_factory=list)
    source_groups: list[dict[str, Any]] = Field(default_factory=list)
    categories: list[dict[str, Any]] = Field(default_factory=list)
    severities: list[dict[str, Any]] = Field(default_factory=list)
    statuses: list[dict[str, Any]] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    chains: list[str] = Field(default_factory=list)
    languages: list[dict[str, Any]] = Field(default_factory=list)


class SavedSearchCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    query: EventSearchParams


class SavedSearchPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    query: EventSearchParams | None = None


class SavedSearchRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None = None
    query: dict[str, Any]
    created_by: str
    created_at: datetime
    updated_at: datetime
