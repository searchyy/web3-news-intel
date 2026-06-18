from __future__ import annotations

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    app: str


class ReloadResponse(BaseModel):
    loaded: int
    enabled: int


class RepublishResponse(BaseModel):
    event_id: int
    queued: bool
