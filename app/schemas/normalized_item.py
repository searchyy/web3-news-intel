from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.core.time import ensure_utc
from app.core.url_security import validate_public_http_url


class NormalizedItem(BaseModel):
    title: str
    summary: str | None = None
    url: str
    canonical_url: str | None = None
    published_at: datetime | None = None
    source_key: str
    source_type: str
    category: str
    language: str | None = None
    symbols: list[str] = Field(default_factory=list)
    chains: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)

    @field_validator("published_at")
    @classmethod
    def published_at_utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value)

    @field_validator("url")
    @classmethod
    def validate_item_url(cls, value: str) -> str:
        _validate_item_url(value, "item URL")
        return value

    @field_validator("canonical_url")
    @classmethod
    def validate_canonical_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        _validate_item_url(value, "canonical URL")
        return value


def _validate_item_url(value: str, label: str) -> None:
    from app.core.config import settings

    allow_localhost = settings.app_env.lower() == "test"
    try:
        validate_public_http_url(
            value,
            allow_private_networks=False,
            allow_localhost=allow_localhost,
            resolve_dns=False,
        )
    except Exception as exc:
        raise ValueError(f"{label} must be public HTTP(S)") from exc
