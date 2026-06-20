from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.admin_auth import (
    AdminPrincipal,
    clear_session_cookies,
    ip_hash,
    request_id,
    require_admin_session,
    require_csrf,
    session_store,
    set_session_cookies,
    verify_admin_password,
)
from app.core.config import settings
from app.core.field_encryption import FieldEncryptor
from app.core.time import utc_now
from app.db.models import (
    AdminAuditLog,
    Delivery,
    Event,
    FetchRun,
    NotificationDestination,
    NotificationRule,
    Source,
)
from app.db.repositories.notification_repo import NotificationRepository, write_audit_log
from app.db.session import get_session
from app.integrations.feishu.client import validate_feishu_webhook_url
from app.publishers.feishu import publish_feishu_once
from app.schemas.admin import (
    AdminAuthResponse,
    AdminLoginRequest,
    AuditLogRead,
    BreakdownPoint,
    DashboardSummary,
    DeliveryRead,
    DestinationCreate,
    DestinationPatch,
    DestinationRead,
    RuleCreate,
    RulePatch,
    RuleRead,
    TimeSeriesPoint,
)
from app.schemas.event import EventDetail, EventRead
from app.schemas.source import SourceRead
from app.workers.tasks_fetch import fetch_source
from app.workers.tasks_publish import republish_event

router = APIRouter(prefix="/api/admin", tags=["admin-api"])


@router.post("/auth/login", response_model=AdminAuthResponse)
def login(
    payload: AdminLoginRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
) -> AdminAuthResponse:
    if not verify_admin_password(payload.username, payload.password, request):
        raise HTTPException(status_code=401, detail="invalid username or password")
    session_id, csrf_token = session_store.create(payload.username)
    set_session_cookies(response, session_id, csrf_token)
    write_audit_log(
        session,
        admin_subject=payload.username,
        action="login",
        resource_type="admin_session",
        resource_id=None,
        metadata={},
        request_id=request_id(request),
        ip_hash=ip_hash(request),
    )
    session.commit()
    return AdminAuthResponse(authenticated=True, username=payload.username, csrf_token=csrf_token)


@router.post("/auth/logout", response_model=AdminAuthResponse)
def logout(
    request: Request,
    response: Response,
    _: None = Depends(require_csrf),
    principal: AdminPrincipal = Depends(require_admin_session),
) -> AdminAuthResponse:
    session_store.delete(principal.session_id)
    clear_session_cookies(response)
    return AdminAuthResponse(authenticated=False, username=principal.subject)


@router.get("/auth/me", response_model=AdminAuthResponse)
def me(principal: AdminPrincipal = Depends(require_admin_session)) -> AdminAuthResponse:
    return AdminAuthResponse(
        authenticated=True,
        username=principal.subject,
        csrf_token=principal.csrf_token,
    )


@router.get("/dashboard/summary", response_model=DashboardSummary)
def dashboard_summary(
    _: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> DashboardSummary:
    now = datetime.now(UTC)
    return DashboardSummary(
        events_last_hour=_count(session, Event, Event.first_seen_at >= now - timedelta(hours=1)),
        events_last_24h=_count(session, Event, Event.first_seen_at >= now - timedelta(hours=24)),
        critical_high_count=_count(session, Event, Event.severity.in_(["critical", "high"])),
        enabled_sources=_count(session, Source, Source.enabled.is_(True)),
        failed_sources=_count(session, Source, Source.access_denied_at.is_not(None)),
        successful_deliveries=_count(session, Delivery, Delivery.status == "delivered"),
        failed_deliveries=_count(session, Delivery, Delivery.status == "failed"),
        pending_feishu_groups=_count(
            session,
            NotificationDestination,
            NotificationDestination.provider == "feishu_app",
            NotificationDestination.status == "pending",
        ),
    )


@router.get("/dashboard/event-volume", response_model=list[TimeSeriesPoint])
def event_volume(
    _: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> list[TimeSeriesPoint]:
    now = datetime.now(UTC)
    points = []
    for hour in range(23, -1, -1):
        start = now - timedelta(hours=hour + 1)
        end = now - timedelta(hours=hour)
        points.append(
            TimeSeriesPoint(
                timestamp=end,
                count=_count(
                    session,
                    Event,
                    Event.first_seen_at >= start,
                    Event.first_seen_at < end,
                ),
            )
        )
    return points


@router.get("/dashboard/category-breakdown", response_model=list[BreakdownPoint])
def category_breakdown(
    _: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> list[BreakdownPoint]:
    rows = session.execute(select(Event.category, func.count(Event.id)).group_by(Event.category))
    return [BreakdownPoint(key=str(key), count=int(count)) for key, count in rows]


@router.get("/dashboard/delivery-health", response_model=list[BreakdownPoint])
def delivery_health(
    _: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> list[BreakdownPoint]:
    rows = session.execute(
        select(Delivery.status, func.count(Delivery.id)).group_by(Delivery.status)
    )
    return [BreakdownPoint(key=str(key), count=int(count)) for key, count in rows]


@router.get("/dashboard/source-health", response_model=list[BreakdownPoint])
def source_health(
    _: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> list[BreakdownPoint]:
    enabled = _count(session, Source, Source.enabled.is_(True))
    disabled = _count(session, Source, Source.enabled.is_(False))
    access_denied = _count(session, Source, Source.access_denied_at.is_not(None))
    return [
        BreakdownPoint(key="enabled", count=enabled),
        BreakdownPoint(key="disabled", count=disabled),
        BreakdownPoint(key="access_denied", count=access_denied),
    ]


@router.get("/events", response_model=list[EventRead])
def admin_events(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    severity: str | None = None,
    status_value: str | None = Query(default=None, alias="status"),
    category: str | None = None,
    sort: str = Query(default="published_at_desc"),
    _: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> list[EventRead]:
    order = {
        "published_at_desc": Event.published_at.desc().nullslast(),
        "first_seen_at_desc": Event.first_seen_at.desc(),
        "severity_desc": Event.severity.desc(),
    }.get(sort)
    if order is None:
        raise HTTPException(status_code=400, detail="unsupported sort")
    stmt = select(Event).order_by(order).offset(offset).limit(limit)
    if severity:
        stmt = stmt.where(Event.severity == severity)
    if status_value:
        stmt = stmt.where(Event.status == status_value)
    if category:
        stmt = stmt.where(Event.category == category)
    return [EventRead.model_validate(event) for event in session.scalars(stmt)]


@router.get("/events/{event_id}", response_model=EventDetail)
def admin_event_detail(
    event_id: int,
    _: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> EventDetail:
    event = session.scalar(
        select(Event).options(selectinload(Event.sources)).where(Event.id == event_id)
    )
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    return EventDetail.model_validate(event)


@router.post("/events/{event_id}/republish")
def admin_republish_event(
    event_id: int,
    request: Request,
    _: None = Depends(require_csrf),
    principal: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> dict[str, bool | int]:
    if session.get(Event, event_id) is None:
        raise HTTPException(status_code=404, detail="event not found")
    republish_event.delay(event_id)
    _audit(session, principal, request, "republish", "event", str(event_id), {})
    session.commit()
    return {"event_id": event_id, "queued": True}


@router.post("/events/{event_id}/acknowledge")
def acknowledge_event(
    event_id: int,
    request: Request,
    _: None = Depends(require_csrf),
    principal: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> dict[str, bool | int]:
    event = session.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    event.status = "acknowledged"
    _audit(session, principal, request, "acknowledge", "event", str(event_id), {})
    session.commit()
    return {"event_id": event_id, "acknowledged": True}


@router.get("/sources", response_model=list[SourceRead])
def admin_sources(
    _: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> list[SourceRead]:
    return [SourceRead.model_validate(source) for source in session.scalars(select(Source))]


@router.patch("/sources/{source_id}", response_model=SourceRead)
def patch_source(
    source_id: int,
    payload: dict,
    request: Request,
    _: None = Depends(require_csrf),
    principal: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> SourceRead:
    source = session.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="source not found")
    for field in {"enabled", "poll_seconds", "timeout_seconds", "max_response_bytes"}:
        if field in payload:
            setattr(source, field, payload[field])
    _audit(
        session,
        principal,
        request,
        "update",
        "source",
        str(source_id),
        {"fields": list(payload)},
    )
    session.commit()
    return SourceRead.model_validate(source)


@router.post("/sources/{source_id}/run")
def run_source(
    source_id: int,
    request: Request,
    _: None = Depends(require_csrf),
    principal: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> dict[str, bool | str]:
    source = session.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="source not found")
    fetch_source.delay(source.key)
    _audit(session, principal, request, "run", "source", str(source_id), {"source_key": source.key})
    session.commit()
    return {"queued": True, "source_key": source.key}


@router.get("/sources/{source_id}/runs")
def source_runs(
    source_id: int,
    _: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> list[dict]:
    rows = session.scalars(
        select(FetchRun)
        .where(FetchRun.source_id == source_id)
        .order_by(FetchRun.started_at.desc())
        .limit(100)
    )
    return [
        {
            "id": row.id,
            "status": row.status,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "http_status": row.http_status,
            "item_count": row.item_count,
            "error_code": row.error_code,
            "error_message": row.error_message,
        }
        for row in rows
    ]


@router.get("/destinations", response_model=list[DestinationRead])
def list_destinations(
    _: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> list[DestinationRead]:
    return [
        DestinationRead.model_validate(item)
        for item in NotificationRepository(session).list_destinations()
    ]


@router.post("/destinations", response_model=DestinationRead)
def create_destination(
    payload: DestinationCreate,
    request: Request,
    _: None = Depends(require_csrf),
    principal: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> DestinationRead:
    repo = NotificationRepository(session)
    if payload.provider == "feishu_webhook":
        if not settings.field_encryption_key:
            raise HTTPException(status_code=503, detail="field encryption is not configured")
        if not payload.webhook_url:
            raise HTTPException(status_code=400, detail="webhook_url is required")
        validate_feishu_webhook_url(payload.webhook_url)
        destination = repo.create_webhook_destination(
            key=payload.key,
            name=payload.name,
            webhook_url=payload.webhook_url,
            encryptor=FieldEncryptor(settings.field_encryption_key),
            config={k: v for k, v in payload.config.items() if "secret" not in k.lower()},
        )
    else:
        destination = NotificationDestination(
            key=payload.key,
            name=payload.name,
            provider=payload.provider,
            enabled=payload.enabled,
            status="pending",
            chat_id=payload.chat_id,
            chat_name=payload.chat_name,
            config=payload.config,
        )
        session.add(destination)
        session.flush()
    _audit(
        session,
        principal,
        request,
        "create",
        "destination",
        str(destination.id),
        {"provider": payload.provider},
    )
    session.commit()
    return DestinationRead.model_validate(destination)


@router.get("/destinations/{destination_id}", response_model=DestinationRead)
def get_destination(
    destination_id: uuid.UUID,
    _: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> DestinationRead:
    destination = _destination_or_404(session, destination_id)
    return DestinationRead.model_validate(destination)


@router.patch("/destinations/{destination_id}", response_model=DestinationRead)
def patch_destination(
    destination_id: uuid.UUID,
    payload: DestinationPatch,
    request: Request,
    _: None = Depends(require_csrf),
    principal: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> DestinationRead:
    destination = _destination_or_404(session, destination_id)
    update = payload.model_dump(exclude_unset=True)
    if "webhook_url" in update:
        if destination.provider != "feishu_webhook":
            raise HTTPException(
                status_code=400,
                detail="webhook_url is only valid for Feishu webhook",
            )
        if not settings.field_encryption_key:
            raise HTTPException(status_code=503, detail="field encryption is not configured")
        webhook_url = update.pop("webhook_url")
        validate_feishu_webhook_url(webhook_url)
        destination.secret_ciphertext = FieldEncryptor(settings.field_encryption_key).encrypt(
            webhook_url
        )
    for field, value in update.items():
        if field == "config" and isinstance(value, dict):
            value = {k: v for k, v in value.items() if "secret" not in k.lower()}
        setattr(destination, field, value)
    _audit(
        session,
        principal,
        request,
        "update",
        "destination",
        str(destination_id),
        {"fields": list(update)},
    )
    session.commit()
    return DestinationRead.model_validate(destination)


@router.delete("/destinations/{destination_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_destination(
    destination_id: uuid.UUID,
    request: Request,
    _: None = Depends(require_csrf),
    principal: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> Response:
    destination = _destination_or_404(session, destination_id)
    NotificationRepository(session).disable(destination)
    _audit(session, principal, request, "disable", "destination", str(destination_id), {})
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/destinations/{destination_id}/approve", response_model=DestinationRead)
def approve_destination(
    destination_id: uuid.UUID,
    request: Request,
    _: None = Depends(require_csrf),
    principal: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> DestinationRead:
    destination = _destination_or_404(session, destination_id)
    NotificationRepository(session).approve(destination)
    _audit(session, principal, request, "approve", "destination", str(destination_id), {})
    session.commit()
    return DestinationRead.model_validate(destination)


@router.post("/destinations/{destination_id}/enable", response_model=DestinationRead)
def enable_destination(
    destination_id: uuid.UUID,
    request: Request,
    _: None = Depends(require_csrf),
    principal: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> DestinationRead:
    destination = _destination_or_404(session, destination_id)
    destination.enabled = True
    if destination.status == "pending":
        destination.status = "active"
    destination.activated_at = destination.activated_at or utc_now()
    _audit(session, principal, request, "enable", "destination", str(destination_id), {})
    session.commit()
    return DestinationRead.model_validate(destination)


@router.post("/destinations/{destination_id}/disable", response_model=DestinationRead)
def disable_destination(
    destination_id: uuid.UUID,
    request: Request,
    _: None = Depends(require_csrf),
    principal: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> DestinationRead:
    destination = _destination_or_404(session, destination_id)
    NotificationRepository(session).disable(destination)
    _audit(session, principal, request, "disable", "destination", str(destination_id), {})
    session.commit()
    return DestinationRead.model_validate(destination)


@router.post("/destinations/{destination_id}/test", response_model=DeliveryRead)
def test_destination(
    destination_id: uuid.UUID,
    request: Request,
    _: None = Depends(require_csrf),
    principal: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> DeliveryRead:
    destination = _destination_or_404(session, destination_id)
    event = _ensure_test_event(session, destination)
    delivery = asyncio.run(
        publish_feishu_once(
            session,
            event,
            destination,
            encryptor=(
                FieldEncryptor(settings.field_encryption_key)
                if settings.field_encryption_key
                else None
            ),
            delivery_variant="test",
            test_send=True,
        )
    )
    destination.last_tested_at = utc_now()
    _audit(session, principal, request, "test", "destination", str(destination_id), {})
    session.commit()
    return DeliveryRead.model_validate(delivery)


@router.get("/rules", response_model=list[RuleRead])
def list_rules(
    _: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> list[RuleRead]:
    return [RuleRead.model_validate(rule) for rule in session.scalars(select(NotificationRule))]


@router.post("/rules", response_model=RuleRead)
def create_rule(
    payload: RuleCreate,
    request: Request,
    _: None = Depends(require_csrf),
    principal: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> RuleRead:
    if session.get(NotificationDestination, payload.destination_id) is None:
        raise HTTPException(status_code=404, detail="destination not found")
    rule = NotificationRule(**payload.model_dump())
    session.add(rule)
    session.flush()
    _audit(session, principal, request, "create", "rule", str(rule.id), {})
    session.commit()
    return RuleRead.model_validate(rule)


@router.patch("/rules/{rule_id}", response_model=RuleRead)
def patch_rule(
    rule_id: uuid.UUID,
    payload: RulePatch,
    request: Request,
    _: None = Depends(require_csrf),
    principal: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> RuleRead:
    rule = session.get(NotificationRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="rule not found")
    update = payload.model_dump(exclude_unset=True)
    for field, value in update.items():
        setattr(rule, field, value)
    _audit(session, principal, request, "update", "rule", str(rule_id), {"fields": list(update)})
    session.commit()
    return RuleRead.model_validate(rule)


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(
    rule_id: uuid.UUID,
    request: Request,
    _: None = Depends(require_csrf),
    principal: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> Response:
    rule = session.get(NotificationRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="rule not found")
    session.delete(rule)
    _audit(session, principal, request, "delete", "rule", str(rule_id), {})
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/deliveries", response_model=list[DeliveryRead])
def list_deliveries(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status_value: str | None = Query(default=None, alias="status"),
    _: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> list[DeliveryRead]:
    stmt = select(Delivery).order_by(Delivery.created_at.desc()).offset(offset).limit(limit)
    if status_value:
        stmt = stmt.where(Delivery.status == status_value)
    return [DeliveryRead.model_validate(item) for item in session.scalars(stmt)]


@router.get("/deliveries/{delivery_id}", response_model=DeliveryRead)
def get_delivery(
    delivery_id: int,
    _: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> DeliveryRead:
    delivery = session.get(Delivery, delivery_id)
    if delivery is None:
        raise HTTPException(status_code=404, detail="delivery not found")
    return DeliveryRead.model_validate(delivery)


@router.post("/deliveries/{delivery_id}/retry")
def retry_delivery(
    delivery_id: int,
    request: Request,
    _: None = Depends(require_csrf),
    principal: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> dict[str, bool | int]:
    delivery = session.get(Delivery, delivery_id)
    if delivery is None:
        raise HTTPException(status_code=404, detail="delivery not found")
    delivery.status = "pending"
    republish_event.delay(delivery.event_id)
    _audit(session, principal, request, "retry", "delivery", str(delivery_id), {})
    session.commit()
    return {"delivery_id": delivery_id, "queued": True}


@router.get("/system/health")
def system_health(_: AdminPrincipal = Depends(require_admin_session)) -> dict:
    return {"api": "ok", "postgresql": "configured", "redis": "configured", "celery": "configured"}


@router.get("/system/queues")
def system_queues(_: AdminPrincipal = Depends(require_admin_session)) -> dict:
    return {"queues": [{"name": "web3-news-intel", "depth": None}]}


@router.get("/system/canary-runs")
def canary_runs(_: AdminPrincipal = Depends(require_admin_session)) -> dict:
    return {"runs": []}


@router.get("/audit-logs", response_model=list[AuditLogRead])
def audit_logs(
    limit: int = Query(default=100, ge=1, le=500),
    _: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> list[AuditLogRead]:
    rows = session.scalars(
        select(AdminAuditLog).order_by(AdminAuditLog.created_at.desc()).limit(limit)
    )
    return [AuditLogRead.model_validate(row) for row in rows]


def _count(session: Session, model, *conditions) -> int:
    stmt = select(func.count(model.id))
    for condition in conditions:
        stmt = stmt.where(condition)
    return int(session.scalar(stmt) or 0)


def _destination_or_404(session: Session, destination_id: uuid.UUID) -> NotificationDestination:
    destination = session.get(NotificationDestination, destination_id)
    if destination is None:
        raise HTTPException(status_code=404, detail="destination not found")
    return destination


def _ensure_test_event(session: Session, destination: NotificationDestination) -> Event:
    key = f"feishu-test:{destination.id}"
    event = session.scalar(select(Event).where(Event.event_key == key))
    if event is not None:
        return event
    event = Event(
        event_key=key,
        title="Feishu integration test card",
        summary="This is a harmless test message from web3-news-intel.",
        category="system",
        status="confirmed",
        severity="low",
        language="en",
        primary_url=settings.public_base_url,
        published_at=utc_now(),
        first_seen_at=utc_now(),
        last_seen_at=utc_now(),
        trust_score=100,
        confirmation_count=1,
        symbols=[],
        chains=[],
        entities=[],
        metadata_={"test": True},
    )
    session.add(event)
    session.flush()
    return event


def _audit(
    session: Session,
    principal: AdminPrincipal,
    request: Request,
    action: str,
    resource_type: str,
    resource_id: str | None,
    metadata: dict,
) -> None:
    write_audit_log(
        session,
        admin_subject=principal.subject,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        metadata=metadata,
        request_id=request_id(request),
        ip_hash=ip_hash(request),
    )
