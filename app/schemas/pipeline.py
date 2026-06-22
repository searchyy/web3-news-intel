from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

FetchPipelineStatus = Literal[
    "queued",
    "fetching",
    "fetched",
    "parse_failed",
    "access_denied",
    "network_failed",
]
EventPipelineStatus = Literal["created", "merged", "confirmed", "needs_review", "rejected"]
AIPipelineStatus = Literal[
    "not_requested",
    "queued",
    "started",
    "retrying",
    "succeeded",
    "failed",
    "cancelled",
    "skipped",
]
DeliveryPipelineStatus = Literal[
    "not_routed",
    "queued",
    "sending",
    "delivered",
    "failed",
    "suppressed",
    "rate_limited",
    "dry_run",
]


class PipelineSourceStage(BaseModel):
    status: FetchPipelineStatus
    source_key: str | None = None
    source_name: str | None = None
    fetch_run_id: int | None = None
    raw_document_id: int | None = None
    queue_wait_ms: int | None = None
    fetch_duration_ms: int | None = None
    parse_duration_ms: int | None = None
    database_duration_ms: int | None = None
    total_duration_ms: int | None = None
    http_status: int | None = None
    error_code: str | None = None
    error_message: str | None = None


class PipelineEventStage(BaseModel):
    status: EventPipelineStatus
    event_key: str
    confirmation_count: int
    source_count: int
    first_seen_at: datetime
    last_seen_at: datetime


class PipelineAIStage(BaseModel):
    status: AIPipelineStatus
    job_id: int | None = None
    task_id: str | None = None
    input_quality: str | None = None
    queue_wait_ms: int | None = None
    provider_latency_ms: int | None = None
    total_latency_ms: int | None = None
    retry_count: int = 0
    model: str | None = None
    generated_at: datetime | None = None
    error_code: str | None = None
    error_message_sanitized: str | None = None


class PipelineDeliveryStage(BaseModel):
    destination_id: UUID | None = None
    destination_name: str | None = None
    destination_key: str | None = None
    status: DeliveryPipelineStatus
    delivery_id: int | None = None
    delivery_variant: str | None = None
    attempts: int = 0
    response_status: int | None = None
    delivered_at: datetime | None = None
    error_message_sanitized: str | None = None


class EventPipelineRead(BaseModel):
    event_id: int
    source: PipelineSourceStage
    event: PipelineEventStage
    ai: PipelineAIStage
    deliveries: list[PipelineDeliveryStage] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
