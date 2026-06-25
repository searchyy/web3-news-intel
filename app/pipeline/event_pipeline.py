from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.time import ensure_utc
from app.db.models import AIRun, Delivery, Event, EventAIInsight, EventSource, FetchRun, RawDocument
from app.schemas.pipeline import (
    EventPipelineRead,
    PipelineAIStage,
    PipelineDeliveryStage,
    PipelineEventStage,
    PipelineSourceStage,
)

ACTIVE_AI_STATUSES = {"queued", "started", "retrying"}


def load_event_for_pipeline(session: Session, event_id: int) -> Event | None:
    return session.scalar(
        select(Event)
        .options(
            selectinload(Event.sources).selectinload(EventSource.source),
            selectinload(Event.sources)
            .selectinload(EventSource.raw_document)
            .selectinload(RawDocument.fetch_run),
            selectinload(Event.ai_insights),
            selectinload(Event.deliveries).selectinload(Delivery.destination),
        )
        .where(Event.id == event_id)
    )


def build_event_pipeline(session: Session, event: Event) -> EventPipelineRead:
    source_stage = _source_stage(event)
    event_stage = PipelineEventStage(
        status=_event_status(event),
        event_key=event.event_key,
        confirmation_count=event.confirmation_count,
        source_count=len(event.sources or []),
        first_seen_at=event.first_seen_at,
        last_seen_at=event.last_seen_at,
    )
    ai_stage = _ai_stage(session, event)
    deliveries = [
        _delivery_stage(delivery)
        for delivery in sorted(event.deliveries, key=_delivery_sort_key)
    ]
    metrics = _metrics(source_stage, ai_stage, deliveries)
    return EventPipelineRead(
        event_id=event.id,
        source=source_stage,
        event=event_stage,
        ai=ai_stage,
        deliveries=deliveries,
        metrics=metrics,
    )


def _source_stage(event: Event) -> PipelineSourceStage:
    event_sources = sorted(event.sources or [], key=lambda item: item.id or 0)
    chosen_source = event_sources[0] if event_sources else None
    raw_document = chosen_source.raw_document if chosen_source else None
    fetch_run = raw_document.fetch_run if raw_document else None
    if fetch_run is None:
        return PipelineSourceStage(
            status="fetched" if chosen_source is not None else "queued",
            source_key=chosen_source.source.key if chosen_source and chosen_source.source else None,
            source_name=(
                chosen_source.source.name if chosen_source and chosen_source.source else None
            ),
            raw_document_id=raw_document.id if raw_document else None,
        )
    return PipelineSourceStage(
        status=_fetch_status(fetch_run),
        source_key=chosen_source.source.key if chosen_source and chosen_source.source else None,
        source_name=chosen_source.source.name if chosen_source and chosen_source.source else None,
        fetch_run_id=fetch_run.id,
        raw_document_id=raw_document.id if raw_document else None,
        queue_wait_ms=_duration_ms(fetch_run.queued_at, fetch_run.worker_started_at),
        fetch_duration_ms=_duration_ms(
            fetch_run.worker_started_at or fetch_run.started_at,
            fetch_run.finished_at,
        ),
        parse_duration_ms=None,
        database_duration_ms=None,
        total_duration_ms=_duration_ms(
            fetch_run.queued_at or fetch_run.started_at,
            fetch_run.finished_at,
        ),
        http_status=fetch_run.http_status,
        error_code=fetch_run.error_code,
        error_message=_sanitize_pipeline_error(fetch_run.error_message),
    )


def _fetch_status(fetch_run: FetchRun) -> str:
    status = fetch_run.status
    if status == "queued":
        return "queued"
    if status == "running":
        return "fetching"
    if status in {"success", "not_modified", "skipped"}:
        return "fetched"
    if status == "access_denied":
        return "access_denied"
    if status == "failed" and fetch_run.error_code and "parse" in fetch_run.error_code:
        return "parse_failed"
    return "network_failed"


def _event_status(event: Event) -> str:
    if event.status == "rejected":
        return "rejected"
    if event.status in {"needs_review", "review"}:
        return "needs_review"
    if event.confirmation_count > 1:
        return "merged"
    if event.status in {"confirmed", "acknowledged"}:
        return "confirmed"
    return "created"


def _ai_stage(session: Session, event: Event) -> PipelineAIStage:
    latest_job = _latest_ai_job(session, event.id)
    latest_insight = _latest_insight(event)
    if latest_job is not None and latest_job.status in ACTIVE_AI_STATUSES | {"failed", "cancelled"}:
        return PipelineAIStage(
            status=_ai_status(latest_job.status),
            job_id=latest_job.id,
            task_id=latest_job.task_id,
            input_quality=latest_insight.input_quality if latest_insight else None,
            queue_wait_ms=latest_job.queue_wait_ms,
            provider_latency_ms=latest_job.provider_latency_ms,
            total_latency_ms=latest_job.total_latency_ms,
            retry_count=latest_job.retry_count,
            model=latest_job.model,
            error_code=latest_job.error_code,
            error_message_sanitized=(
                latest_job.error_message_sanitized or latest_job.error_sanitized
            ),
        )
    if latest_insight is not None and latest_insight.status in {"succeeded", "success"}:
        return PipelineAIStage(
            status="succeeded",
            input_quality=latest_insight.input_quality,
            queue_wait_ms=latest_job.queue_wait_ms if latest_job else None,
            provider_latency_ms=latest_job.provider_latency_ms if latest_job else None,
            total_latency_ms=latest_job.total_latency_ms if latest_job else None,
            retry_count=latest_job.retry_count if latest_job else 0,
            model=latest_insight.model,
            generated_at=latest_insight.generated_at,
        )
    if latest_job is not None:
        return PipelineAIStage(
            status=_ai_status(latest_job.status),
            job_id=latest_job.id,
            task_id=latest_job.task_id,
            queue_wait_ms=latest_job.queue_wait_ms,
            provider_latency_ms=latest_job.provider_latency_ms,
            total_latency_ms=latest_job.total_latency_ms,
            retry_count=latest_job.retry_count,
            model=latest_job.model,
            error_code=latest_job.error_code,
            error_message_sanitized=(
                latest_job.error_message_sanitized or latest_job.error_sanitized
            ),
        )
    return PipelineAIStage(status="not_requested")


def _latest_ai_job(session: Session, event_id: int) -> AIRun | None:
    rows = session.scalars(
        select(AIRun)
        .where(AIRun.job_type.in_(["summarize_event", "summarize_event_batch"]))
        .order_by(AIRun.created_at.desc())
        .limit(200)
    )
    for row in rows:
        values = row.event_ids if isinstance(row.event_ids, list) else []
        if event_id in {int(item) for item in values if _int_like(item)}:
            return row
    return None


def _latest_insight(event: Event) -> EventAIInsight | None:
    insights = sorted(
        event.ai_insights or [],
        key=lambda item: item.generated_at or item.id or datetime.min,
        reverse=True,
    )
    return insights[0] if insights else None


def _ai_status(status: str) -> str:
    known = {"queued", "started", "retrying", "succeeded", "failed", "cancelled"}
    return {
        "running": "started",
        "success": "succeeded",
        "budget_rejected": "failed",
    }.get(status, status if status in known else "failed")


def _delivery_stage(delivery: Delivery) -> PipelineDeliveryStage:
    destination = delivery.destination
    return PipelineDeliveryStage(
        destination_id=delivery.destination_id,
        destination_name=destination.name if destination else None,
        destination_key=destination.key if destination else None,
        status=_delivery_status(delivery),
        delivery_id=delivery.id,
        delivery_variant=delivery.delivery_variant,
        attempts=delivery.attempts,
        response_status=delivery.response_status,
        delivered_at=delivery.delivered_at,
        error_message_sanitized=_sanitize_pipeline_error(delivery.last_error),
    )


def _delivery_status(delivery: Delivery) -> str:
    if delivery.provider_message_id == "dry-run":
        return "dry_run"
    return {
        "pending": "queued",
        "sending": "sending",
        "delivered": "delivered",
        "failed": "failed",
        "suppressed": "suppressed",
        "rate_limited": "rate_limited",
    }.get(delivery.status, "failed")


def _metrics(
    source: PipelineSourceStage,
    ai: PipelineAIStage,
    deliveries: list[PipelineDeliveryStage],
) -> dict[str, Any]:
    delivered = [item for item in deliveries if item.status == "delivered"]
    return {
        "source_queue_wait_ms": source.queue_wait_ms,
        "fetch_duration_ms": source.fetch_duration_ms,
        "parse_duration_ms": source.parse_duration_ms,
        "event_database_ms": source.database_duration_ms,
        "ai_queue_wait_ms": ai.queue_wait_ms,
        "ai_provider_latency_ms": ai.provider_latency_ms,
        "ai_total_latency_ms": ai.total_latency_ms,
        "delivery_count": len(deliveries),
        "delivered_count": len(delivered),
    }


def _duration_ms(start: datetime | None, end: datetime | None) -> int | None:
    start_utc = ensure_utc(start)
    end_utc = ensure_utc(end)
    if start_utc is None or end_utc is None:
        return None
    return max(0, int((end_utc - start_utc).total_seconds() * 1000))


def _sanitize_pipeline_error(value: str | None) -> str | None:
    if not value:
        return None
    lowered = value.lower()
    blocked_markers = (
        "token",
        "secret",
        "webhook",
        "authorization",
        "cookie",
        "password",
        "api key",
    )
    if any(marker in lowered for marker in blocked_markers):
        return "错误信息已脱敏"
    return value[:300]


def _delivery_sort_key(delivery: Delivery) -> tuple[datetime, int]:
    return (delivery.created_at or datetime.min, delivery.id or 0)


def _int_like(value: Any) -> bool:
    try:
        int(value)
    except (TypeError, ValueError):
        return False
    return True
