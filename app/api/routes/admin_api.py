from __future__ import annotations

import asyncio
import importlib
import time
import uuid
from datetime import UTC, datetime, timedelta

from celery.result import AsyncResult
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
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
from app.core.config import load_runtime_sources, settings
from app.core.field_encryption import FieldEncryptor
from app.core.time import utc_now
from app.db.models import (
    AdminAuditLog,
    AIRun,
    Delivery,
    Event,
    FetchRun,
    NotificationDestination,
    NotificationRule,
    ReportSchedule,
    Source,
)
from app.db.repositories.event_search_repo import EventSearchRepository, SavedSearchRepository
from app.db.repositories.notification_repo import NotificationRepository, write_audit_log
from app.db.repositories.source_repo import SourceRepository
from app.db.repositories.system_config_repo import SystemConfigRepository
from app.db.session import get_session
from app.integrations.ai.deepseek.errors import AIProviderError
from app.integrations.ai.service import (
    AIService,
    provider_config_to_public_dict,
    sanitize_error,
)
from app.integrations.feishu.client import FeishuClient, validate_feishu_webhook_url
from app.integrations.feishu.errors import FeishuAuthenticationError
from app.integrations.feishu.report_cards import event_ai_summary
from app.integrations.feishu.reporting import FeishuReportService, next_run_at
from app.integrations.feishu.token_provider import FeishuTokenProvider
from app.publishers.feishu import publish_feishu_once
from app.scheduler.planner import mark_source_queued
from app.schemas.admin import (
    AdminAuthResponse,
    AdminLoginRequest,
    AuditLogPage,
    AuditLogRead,
    BreakdownPoint,
    DashboardSummary,
    DeliveryPage,
    DeliveryRead,
    DestinationCreate,
    DestinationPatch,
    DestinationRead,
    FeishuConfigRead,
    FeishuConfigWrite,
    FeishuTestResult,
    RuleCreate,
    RulePatch,
    RuleRead,
    TimeSeriesPoint,
)
from app.schemas.ai import (
    AIBatchSummaryRequest,
    AIModelRead,
    AIProviderConfigRead,
    AIProviderConfigWrite,
    AIQueuedTask,
    AIRunPage,
    AIRunRead,
    AISummaryRequest,
    AITaskStatus,
    AITestResult,
    EventAIInsightRead,
)
from app.schemas.event import EventDetail
from app.schemas.event_search import (
    EventFacets,
    EventSearchPage,
    EventSearchParams,
    SavedSearchCreate,
    SavedSearchPatch,
    SavedSearchRead,
)
from app.schemas.feishu_report import (
    ReportEventPreview,
    ReportPreviewRead,
    ReportRunResponse,
    ReportScheduleCreate,
    ReportSchedulePatch,
    ReportScheduleRead,
    ReportSendResultRead,
)
from app.schemas.source import SourceRead
from app.workers.celery_app import celery_app
from app.workers.tasks_feishu_reports import run_feishu_report_schedule
from app.workers.tasks_fetch import enqueue_fetch_run
from app.workers.tasks_publish import republish_event

router = APIRouter(prefix="/api/admin", tags=["admin-api"])


class _UnavailableTask:
    id = "ai-unavailable"

    def delay(self, *_args, **_kwargs):
        raise HTTPException(status_code=503, detail="AI task service unavailable")


summarize_event_task = _UnavailableTask()
summarize_event_batch_task = _UnavailableTask()


def cancel_ai_task(_task_id: str) -> None:
    raise HTTPException(status_code=503, detail="AI task service unavailable")


def _optional_import(module_name: str):
    try:
        return importlib.import_module(module_name)
    except ImportError:
        return None


_ai_tasks_module = _optional_import("app.workers.tasks_ai")
if _ai_tasks_module is not None:
    cancel_ai_task = _ai_tasks_module.cancel_ai_task
    summarize_event_task = _ai_tasks_module.summarize_event
    summarize_event_batch_task = _ai_tasks_module.summarize_event_batch


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


@router.get("/events", response_model=EventSearchPage)
def admin_events(
    q: str | None = None,
    q_mode: str = Query(default="all", pattern="^(all|any|phrase)$"),
    source_keys: list[str] | None = Query(default=None),
    source_groups: list[str] | None = Query(default=None),
    categories: list[str] | None = Query(default=None),
    severities: list[str] | None = Query(default=None),
    statuses: list[str] | None = Query(default=None),
    symbols: list[str] | None = Query(default=None),
    chains: list[str] | None = Query(default=None),
    languages: list[str] | None = Query(default=None),
    official_only: bool | None = None,
    minimum_trust_score: int | None = Query(default=None, ge=0, le=100),
    has_ai_summary: bool | None = None,
    published_from: datetime | None = None,
    published_to: datetime | None = None,
    first_seen_from: datetime | None = None,
    first_seen_to: datetime | None = None,
    sort: str = Query(default="published_at"),
    direction: str = Query(default="desc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    severity: str | None = None,
    status_value: str | None = Query(default=None, alias="status"),
    category: str | None = None,
    _: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> EventSearchPage:
    params = _event_search_params(
        q=q,
        q_mode=q_mode,
        source_keys=source_keys,
        source_groups=source_groups,
        categories=categories,
        severities=severities,
        statuses=statuses,
        symbols=symbols,
        chains=chains,
        languages=languages,
        official_only=official_only,
        minimum_trust_score=minimum_trust_score,
        has_ai_summary=has_ai_summary,
        published_from=published_from,
        published_to=published_to,
        first_seen_from=first_seen_from,
        first_seen_to=first_seen_to,
        sort=sort,
        direction=direction,
        page=page,
        page_size=page_size,
        limit=limit,
        offset=offset,
        severity=severity,
        status_value=status_value,
        category=category,
    )
    return EventSearchRepository(session).search(params)


@router.get("/events/facets", response_model=EventFacets)
def admin_event_facets(
    q: str | None = None,
    q_mode: str = Query(default="all", pattern="^(all|any|phrase)$"),
    source_keys: list[str] | None = Query(default=None),
    source_groups: list[str] | None = Query(default=None),
    categories: list[str] | None = Query(default=None),
    severities: list[str] | None = Query(default=None),
    statuses: list[str] | None = Query(default=None),
    symbols: list[str] | None = Query(default=None),
    chains: list[str] | None = Query(default=None),
    languages: list[str] | None = Query(default=None),
    official_only: bool | None = None,
    minimum_trust_score: int | None = Query(default=None, ge=0, le=100),
    has_ai_summary: bool | None = None,
    published_from: datetime | None = None,
    published_to: datetime | None = None,
    first_seen_from: datetime | None = None,
    first_seen_to: datetime | None = None,
    sort: str = Query(default="published_at"),
    direction: str = Query(default="desc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    _: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> EventFacets:
    params = _event_search_params(
        q=q,
        q_mode=q_mode,
        source_keys=source_keys,
        source_groups=source_groups,
        categories=categories,
        severities=severities,
        statuses=statuses,
        symbols=symbols,
        chains=chains,
        languages=languages,
        official_only=official_only,
        minimum_trust_score=minimum_trust_score,
        has_ai_summary=has_ai_summary,
        published_from=published_from,
        published_to=published_to,
        first_seen_from=first_seen_from,
        first_seen_to=first_seen_to,
        sort=sort,
        direction=direction,
        page=page,
        page_size=page_size,
    )
    return EventSearchRepository(session).facets(params)


@router.post("/saved-searches", response_model=SavedSearchRead, status_code=status.HTTP_201_CREATED)
def create_saved_search(
    payload: SavedSearchCreate,
    request: Request,
    _: None = Depends(require_csrf),
    principal: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> SavedSearchRead:
    repo = SavedSearchRepository(session)
    try:
        saved = repo.create(principal.subject, payload)
        _audit(session, principal, request, "create", "saved_search", str(saved.id), {})
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="saved search name already exists") from exc
    return SavedSearchRead.model_validate(saved)


@router.get("/saved-searches", response_model=list[SavedSearchRead])
def list_saved_searches(
    principal: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> list[SavedSearchRead]:
    return [
        SavedSearchRead.model_validate(saved)
        for saved in SavedSearchRepository(session).list(principal.subject)
    ]


@router.patch("/saved-searches/{saved_search_id}", response_model=SavedSearchRead)
def patch_saved_search(
    saved_search_id: int,
    payload: SavedSearchPatch,
    request: Request,
    _: None = Depends(require_csrf),
    principal: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> SavedSearchRead:
    repo = SavedSearchRepository(session)
    saved = repo.get_for_owner(saved_search_id, principal.subject)
    if saved is None:
        raise HTTPException(status_code=404, detail="saved search not found")
    try:
        saved = repo.update(saved, payload)
        _audit(session, principal, request, "update", "saved_search", str(saved.id), {})
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="saved search name already exists") from exc
    return SavedSearchRead.model_validate(saved)


@router.delete("/saved-searches/{saved_search_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_saved_search(
    saved_search_id: int,
    request: Request,
    _: None = Depends(require_csrf),
    principal: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> Response:
    repo = SavedSearchRepository(session)
    saved = repo.get_for_owner(saved_search_id, principal.subject)
    if saved is None:
        raise HTTPException(status_code=404, detail="saved search not found")
    repo.delete(saved)
    _audit(session, principal, request, "delete", "saved_search", str(saved_search_id), {})
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
    _sync_runtime_sources(session)
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
    fetch_run = mark_source_queued(session, source, trace_id=str(uuid.uuid4()), force=True)
    if fetch_run is None:
        _audit(
            session,
            principal,
            request,
            "run",
            "source",
            str(source_id),
            {"source_key": source.key, "queued": False},
        )
        session.commit()
        return {"queued": False, "source_key": source.key}
    _audit(session, principal, request, "run", "source", str(source_id), {"source_key": source.key})
    session.commit()
    queued = enqueue_fetch_run(source.key, fetch_run.id)
    return {"queued": queued, "source_key": source.key}


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
            "queued_at": row.queued_at,
            "worker_started_at": row.worker_started_at,
            "task_id": row.task_id,
            "retry_after_until": row.retry_after_until,
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


@router.get("/system/feishu-config", response_model=FeishuConfigRead)
def get_feishu_config(
    _: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> FeishuConfigRead:
    data = SystemConfigRepository(session).read_feishu_config()
    return FeishuConfigRead.model_validate({**data, "connection_status": "not_tested"})


@router.post("/system/feishu-config", response_model=FeishuConfigRead)
def save_feishu_config(
    payload: FeishuConfigWrite,
    request: Request,
    _: None = Depends(require_csrf),
    principal: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> FeishuConfigRead:
    values = payload.model_dump(by_alias=True)
    try:
        SystemConfigRepository(session).save_feishu_config(
            values,
            encryptor=_field_encryptor_if_configured(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    _audit(session, principal, request, "update", "system", "feishu-config", {})
    session.commit()
    data = SystemConfigRepository(session).read_feishu_config()
    return FeishuConfigRead.model_validate({**data, "connection_status": "not_tested"})


@router.post("/destinations/test-feishu", response_model=FeishuTestResult)
def test_feishu_connection(
    request: Request,
    _: None = Depends(require_csrf),
    principal: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> FeishuTestResult:
    config = SystemConfigRepository(session).read_feishu_plaintext(
        encryptor=_field_encryptor_if_configured()
    )
    result = asyncio.run(_run_feishu_connection_test(config))
    _audit(
        session,
        principal,
        request,
        "test",
        "system",
        "feishu-config",
        {"status": result.status},
    )
    session.commit()
    return result


@router.get("/ai/providers/deepseek", response_model=AIProviderConfigRead)
def get_deepseek_config(
    _: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> AIProviderConfigRead:
    service = AIService(session)
    config = service.get_or_create_provider_config("deepseek")
    session.commit()
    return AIProviderConfigRead.model_validate(
        provider_config_to_public_dict(config, service.usage_today("deepseek"))
    )


@router.put("/ai/providers/deepseek", response_model=AIProviderConfigRead)
def save_deepseek_config(
    payload: AIProviderConfigWrite,
    request: Request,
    _: None = Depends(require_csrf),
    principal: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> AIProviderConfigRead:
    service = AIService(session)
    try:
        config = service.save_provider_config(
            payload.model_dump(exclude_unset=True),
            provider="deepseek",
        )
    except (AIProviderError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=sanitize_error(exc)) from exc
    _audit(
        session,
        principal,
        request,
        "update",
        "system",
        "ai-deepseek-config",
        {"fields": list(payload.model_dump(exclude_unset=True, exclude={"api_key"}))},
    )
    session.commit()
    return AIProviderConfigRead.model_validate(
        provider_config_to_public_dict(config, service.usage_today("deepseek"))
    )


@router.delete("/ai/providers/deepseek/key", response_model=AIProviderConfigRead)
def delete_deepseek_key(
    request: Request,
    _: None = Depends(require_csrf),
    principal: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> AIProviderConfigRead:
    service = AIService(session)
    config = service.delete_provider_key("deepseek")
    _audit(session, principal, request, "delete", "system", "ai-deepseek-key", {})
    session.commit()
    return AIProviderConfigRead.model_validate(
        provider_config_to_public_dict(config, service.usage_today("deepseek"))
    )


@router.post("/ai/providers/deepseek/test", response_model=AITestResult)
async def test_deepseek_connection(
    request: Request,
    _: None = Depends(require_csrf),
    principal: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> AITestResult:
    service = AIService(session)
    try:
        result = await service.test_connection("deepseek")
    except Exception as exc:
        _audit(
            session,
            principal,
            request,
            "test",
            "system",
            "ai-deepseek-config",
            {"status": "failed"},
        )
        session.commit()
        return AITestResult(status="failed", error=sanitize_error(exc))
    _audit(
        session,
        principal,
        request,
        "test",
        "system",
        "ai-deepseek-config",
        {"status": "success"},
    )
    session.commit()
    return AITestResult.model_validate(result)


@router.get("/ai/providers/deepseek/models", response_model=list[AIModelRead])
async def list_deepseek_models(
    _: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> list[AIModelRead]:
    try:
        models = await AIService(session).list_models("deepseek")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=sanitize_error(exc)) from exc
    return [AIModelRead.model_validate(item) for item in models]


@router.get("/ai/runs", response_model=AIRunPage)
def list_ai_runs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    limit: int | None = Query(default=None, ge=1, le=200),
    offset: int | None = Query(default=None, ge=0),
    _: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> AIRunPage:
    actual_limit = limit or page_size
    actual_offset = offset if offset is not None else (page - 1) * page_size
    stmt = select(AIRun).order_by(AIRun.created_at.desc())
    total = int(session.scalar(select(func.count()).select_from(AIRun)) or 0)
    rows = session.scalars(stmt.offset(actual_offset).limit(actual_limit))
    return AIRunPage(
        items=[AIRunRead.model_validate(row) for row in rows],
        total=total,
        page=page if offset is None else (actual_offset // actual_limit) + 1,
        page_size=actual_limit,
    )


@router.post("/events/{event_id}/ai-summary", response_model=AIQueuedTask)
def queue_event_ai_summary(
    event_id: int,
    payload: AISummaryRequest,
    request: Request,
    _: None = Depends(require_csrf),
    principal: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> AIQueuedTask:
    if session.get(Event, event_id) is None:
        raise HTTPException(status_code=404, detail="event not found")
    task = summarize_event_task.delay(event_id, force=payload.force, auto=False)
    _audit(session, principal, request, "run", "event_ai_summary", str(event_id), {})
    session.commit()
    return AIQueuedTask(queued=True, task_id=task.id, event_id=event_id)


@router.post("/events/ai-summary-batch", response_model=AIQueuedTask)
def queue_event_ai_summary_batch(
    payload: AIBatchSummaryRequest,
    request: Request,
    _: None = Depends(require_csrf),
    principal: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> AIQueuedTask:
    existing_count = int(
        session.scalar(select(func.count(Event.id)).where(Event.id.in_(payload.event_ids))) or 0
    )
    if existing_count != len(set(payload.event_ids)):
        raise HTTPException(status_code=404, detail="one or more events not found")
    task = summarize_event_batch_task.delay(payload.event_ids, force=payload.force, auto=False)
    _audit(
        session,
        principal,
        request,
        "run",
        "event_ai_summary_batch",
        None,
        {"event_count": len(payload.event_ids)},
    )
    session.commit()
    return AIQueuedTask(queued=True, task_id=task.id, event_ids=payload.event_ids)


@router.get("/events/{event_id}/ai-insight", response_model=EventAIInsightRead)
def get_event_ai_insight(
    event_id: int,
    _: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> EventAIInsightRead:
    insight = AIService(session).latest_insight(event_id)
    if insight is None:
        raise HTTPException(status_code=404, detail="AI insight not found")
    return EventAIInsightRead.model_validate(insight)


@router.get("/ai/tasks/{task_id}", response_model=AITaskStatus)
def get_ai_task_status(
    task_id: str,
    _: AdminPrincipal = Depends(require_admin_session),
) -> AITaskStatus:
    result = AsyncResult(task_id, app=celery_app)
    payload = None
    if result.ready():
        try:
            payload = result.result
        except Exception as exc:
            payload = {"error": sanitize_error(exc)}
    return AITaskStatus(task_id=task_id, status=result.status, result=payload)


@router.post("/ai/tasks/{task_id}/cancel", response_model=AITaskStatus)
def cancel_ai_task_endpoint(
    task_id: str,
    request: Request,
    _: None = Depends(require_csrf),
    principal: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> AITaskStatus:
    cancel_ai_task(task_id)
    _audit(session, principal, request, "cancel", "ai_task", task_id, {})
    session.commit()
    return AITaskStatus(task_id=task_id, status="REVOKED", result={"cancelled": True})


@router.get("/report-schedules", response_model=list[ReportScheduleRead])
def list_report_schedules(
    destination_id: uuid.UUID | None = None,
    _: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> list[ReportScheduleRead]:
    stmt = select(ReportSchedule).order_by(ReportSchedule.created_at.desc())
    if destination_id:
        stmt = stmt.where(ReportSchedule.destination_id == destination_id)
    return [ReportScheduleRead.model_validate(schedule) for schedule in session.scalars(stmt)]


@router.post("/report-schedules", response_model=ReportScheduleRead)
def create_report_schedule(
    payload: ReportScheduleCreate,
    request: Request,
    _: None = Depends(require_csrf),
    principal: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> ReportScheduleRead:
    destination = _destination_or_404(session, payload.destination_id)
    _ensure_feishu_destination(destination)
    now = utc_now()
    schedule = ReportSchedule(**payload.model_dump())
    if schedule.enabled:
        schedule.activated_at = now
        schedule.next_run_at = next_run_at(schedule, after=now)
    session.add(schedule)
    session.flush()
    _audit(
        session,
        principal,
        request,
        "create",
        "report_schedule",
        str(schedule.id),
        {"destination_id": str(destination.id), "report_type": schedule.report_type},
    )
    session.commit()
    return ReportScheduleRead.model_validate(schedule)


@router.get("/report-schedules/{schedule_id}", response_model=ReportScheduleRead)
def get_report_schedule(
    schedule_id: int,
    _: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> ReportScheduleRead:
    return ReportScheduleRead.model_validate(_report_schedule_or_404(session, schedule_id))


@router.patch("/report-schedules/{schedule_id}", response_model=ReportScheduleRead)
def patch_report_schedule(
    schedule_id: int,
    payload: ReportSchedulePatch,
    request: Request,
    _: None = Depends(require_csrf),
    principal: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> ReportScheduleRead:
    schedule = _report_schedule_or_404(session, schedule_id)
    update = payload.model_dump(exclude_unset=True)
    was_enabled = schedule.enabled
    for field, value in update.items():
        setattr(schedule, field, value)
    now = utc_now()
    if schedule.enabled and not was_enabled:
        schedule.activated_at = now
        schedule.last_window_start = None
        schedule.last_window_end = None
    schedule.next_run_at = next_run_at(schedule, after=now) if schedule.enabled else None
    _audit(
        session,
        principal,
        request,
        "update",
        "report_schedule",
        str(schedule_id),
        {"fields": list(update)},
    )
    session.commit()
    return ReportScheduleRead.model_validate(schedule)


@router.delete("/report-schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_report_schedule(
    schedule_id: int,
    request: Request,
    _: None = Depends(require_csrf),
    principal: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> Response:
    schedule = _report_schedule_or_404(session, schedule_id)
    session.delete(schedule)
    _audit(session, principal, request, "delete", "report_schedule", str(schedule_id), {})
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/report-schedules/{schedule_id}/preview", response_model=ReportPreviewRead)
def preview_report_schedule(
    schedule_id: int,
    _: None = Depends(require_csrf),
    __: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> ReportPreviewRead:
    schedule = _report_schedule_or_404(session, schedule_id)
    preview = FeishuReportService(session).preview(schedule, now=utc_now())
    return _report_preview_read(preview)


@router.post("/report-schedules/{schedule_id}/run", response_model=ReportRunResponse)
def enqueue_report_schedule(
    schedule_id: int,
    request: Request,
    _: None = Depends(require_csrf),
    principal: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> ReportRunResponse:
    _report_schedule_or_404(session, schedule_id)
    run_feishu_report_schedule.delay(str(schedule_id))
    _audit(session, principal, request, "run", "report_schedule", str(schedule_id), {})
    session.commit()
    return ReportRunResponse(schedule_id=schedule_id, queued=True)


@router.post("/report-schedules/{schedule_id}/test-send", response_model=ReportSendResultRead)
def test_send_report_schedule(
    schedule_id: int,
    request: Request,
    _: None = Depends(require_csrf),
    principal: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> ReportSendResultRead:
    schedule = _report_schedule_or_404(session, schedule_id)
    outcome = asyncio.run(
        FeishuReportService(
            session,
            encryptor=_field_encryptor_if_configured(),
        ).send_test_report(schedule)
    )
    _audit(
        session,
        principal,
        request,
        "test_send",
        "report_schedule",
        str(schedule_id),
        {"status": outcome.status, "dry_run": outcome.dry_run},
    )
    session.commit()
    return ReportSendResultRead(
        schedule_id=schedule_id,
        delivery_id=outcome.delivery.id if outcome.delivery else None,
        status=outcome.status,
        dry_run=outcome.dry_run,
        message=outcome.message,
    )


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


@router.get("/deliveries", response_model=DeliveryPage)
def list_deliveries(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    limit: int | None = Query(default=None, ge=1, le=200),
    offset: int | None = Query(default=None, ge=0),
    status_value: str | None = Query(default=None, alias="status"),
    _: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> DeliveryPage:
    actual_limit = limit or page_size
    actual_offset = offset if offset is not None else (page - 1) * page_size
    stmt = select(Delivery)
    if status_value:
        stmt = stmt.where(Delivery.status == status_value)
    total = int(session.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = session.scalars(
        stmt.order_by(Delivery.created_at.desc()).offset(actual_offset).limit(actual_limit)
    )
    return DeliveryPage(
        items=[DeliveryRead.model_validate(item) for item in rows],
        total=total,
        page=page if offset is None else (actual_offset // actual_limit) + 1,
        page_size=actual_limit,
    )


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


@router.get("/audit-logs", response_model=AuditLogPage)
def audit_logs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int | None = Query(default=None, ge=0),
    _: AdminPrincipal = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> AuditLogPage:
    actual_limit = limit or page_size
    actual_offset = offset if offset is not None else (page - 1) * page_size
    total = int(session.scalar(select(func.count()).select_from(AdminAuditLog)) or 0)
    rows = session.scalars(
        select(AdminAuditLog)
        .order_by(AdminAuditLog.created_at.desc())
        .offset(actual_offset)
        .limit(actual_limit)
    )
    return AuditLogPage(
        items=[AuditLogRead.model_validate(row) for row in rows],
        total=total,
        page=page if offset is None else (actual_offset // actual_limit) + 1,
        page_size=actual_limit,
    )


def _event_search_params(
    *,
    q: str | None,
    q_mode: str,
    source_keys: list[str] | None,
    source_groups: list[str] | None,
    categories: list[str] | None,
    severities: list[str] | None,
    statuses: list[str] | None,
    symbols: list[str] | None,
    chains: list[str] | None,
    languages: list[str] | None,
    official_only: bool | None,
    minimum_trust_score: int | None,
    has_ai_summary: bool | None,
    published_from: datetime | None,
    published_to: datetime | None,
    first_seen_from: datetime | None,
    first_seen_to: datetime | None,
    sort: str,
    direction: str,
    page: int,
    page_size: int,
    limit: int | None = None,
    offset: int = 0,
    severity: str | None = None,
    status_value: str | None = None,
    category: str | None = None,
) -> EventSearchParams:
    normalized_sort, normalized_direction = _normalize_event_sort(sort, direction)
    if limit is not None:
        page_size = limit
        page = offset // limit + 1
    return EventSearchParams(
        q=q,
        q_mode=q_mode,
        source_keys=source_keys or [],
        source_groups=source_groups or [],
        categories=_with_legacy_value(categories, category),
        severities=_with_legacy_value(severities, severity),
        statuses=_with_legacy_value(statuses, status_value),
        symbols=symbols or [],
        chains=chains or [],
        languages=languages or [],
        official_only=official_only,
        minimum_trust_score=minimum_trust_score,
        has_ai_summary=has_ai_summary,
        published_from=published_from,
        published_to=published_to,
        first_seen_from=first_seen_from,
        first_seen_to=first_seen_to,
        sort=normalized_sort,
        direction=normalized_direction,
        page=page,
        page_size=page_size,
    )


def _sync_runtime_sources(session: Session) -> None:
    repo = SourceRepository(session)
    try:
        sources = load_runtime_sources()
    except FileNotFoundError:
        return
    for source in sources.values():
        repo.upsert_from_config(source)
    session.commit()


def _with_legacy_value(values: list[str] | None, legacy: str | None) -> list[str]:
    merged = list(values or [])
    if legacy:
        merged.append(legacy)
    return merged


def _normalize_event_sort(sort: str, direction: str) -> tuple[str, str]:
    legacy = {
        "published_at_desc": ("published_at", "desc"),
        "published_at_asc": ("published_at", "asc"),
        "first_seen_at_desc": ("first_seen_at", "desc"),
        "first_seen_at_asc": ("first_seen_at", "asc"),
        "severity_desc": ("severity", "desc"),
        "severity_asc": ("severity", "asc"),
    }
    if sort in legacy:
        return legacy[sort]
    allowed = {
        "published_at",
        "first_seen_at",
        "last_seen_at",
        "trust_score",
        "severity",
        "confirmation_count",
        "id",
    }
    if sort not in allowed:
        raise HTTPException(status_code=400, detail="unsupported sort")
    return sort, direction


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


def _report_schedule_or_404(session: Session, schedule_id: int) -> ReportSchedule:
    schedule = session.get(ReportSchedule, schedule_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail="report schedule not found")
    return schedule


def _ensure_feishu_destination(destination: NotificationDestination) -> None:
    if destination.provider not in {"feishu_app", "feishu_webhook"}:
        raise HTTPException(status_code=400, detail="report schedule requires Feishu destination")


def _report_preview_read(preview) -> ReportPreviewRead:
    return ReportPreviewRead(
        schedule_id=preview.schedule.id,
        destination_id=preview.schedule.destination_id,
        report_type=preview.schedule.report_type,
        window_start=preview.window_start,
        window_end=preview.window_end,
        event_count=preview.event_count,
        critical_high_count=preview.critical_high_count,
        top_symbols=preview.top_symbols,
        top_categories=preview.top_categories,
        summary_zh=preview.summary_zh,
        omitted_count=preview.omitted_count,
        card=preview.card,
        events=[
            ReportEventPreview(
                id=event.id,
                title=event.title,
                severity=event.severity,
                category=event.category,
                published_at=event.published_at,
                first_seen_at=event.first_seen_at,
                primary_url=event.primary_url,
                symbols=event.symbols,
                chains=event.chains,
                ai_summary_zh=event_ai_summary(event),
            )
            for event in preview.events[:10]
        ],
    )


def _ensure_test_event(session: Session, destination: NotificationDestination) -> Event:
    key = f"feishu-test:{destination.id}"
    event = session.scalar(select(Event).where(Event.event_key == key))
    if event is not None:
        return event
    event = Event(
        event_key=key,
        title="飞书集成测试卡片",
        summary="这是一条来自 web3-news-intel 的安全测试消息。",
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


def _field_encryptor_if_configured() -> FieldEncryptor | None:
    if not settings.field_encryption_key:
        return None
    return FieldEncryptor(settings.field_encryption_key)


async def _run_feishu_connection_test(config: dict[str, str | bool | None]) -> FeishuTestResult:
    if not config.get("FEISHU_ENABLED") or not config.get("FEISHU_SEND_ENABLED"):
        return FeishuTestResult(status="failed", error="send_disabled")
    app_id = str(config.get("FEISHU_APP_ID") or "")
    app_secret = str(config.get("FEISHU_APP_SECRET") or "")
    chat_id = str(config.get("FEISHU_TEST_CHAT_ID") or "")
    if not app_id or not app_secret or not chat_id:
        return FeishuTestResult(status="failed", error="missing_config")
    started = time.perf_counter()
    import httpx

    http_client = httpx.AsyncClient(timeout=10, trust_env=False, follow_redirects=False)
    provider = FeishuTokenProvider(
        app_id=app_id,
        app_secret=app_secret,
        client=http_client,
        redis_client=_MemoryAsyncRedis(),
    )
    client = FeishuClient(token_provider=provider, client=http_client)
    try:
        result = await client.send_interactive_card(
            chat_id,
            {
                "config": {"wide_screen_mode": True},
                "header": {
                    "template": "green",
                    "title": {"tag": "plain_text", "content": "web3-news-intel 飞书连接测试"},
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": "这是一条由管理后台发送的安全测试卡片。",
                        },
                    }
                ],
            },
        )
    except FeishuAuthenticationError:
        return FeishuTestResult(status="failed", error="invalid_app_secret")
    except Exception:
        return FeishuTestResult(status="failed", error="network_failed")
    finally:
        await http_client.aclose()
    latency_ms = int((time.perf_counter() - started) * 1000)
    if result.ok:
        return FeishuTestResult(status="success", latency_ms=latency_ms, message="连接成功")
    return FeishuTestResult(
        status="failed",
        latency_ms=latency_ms,
        error="send_failed",
    )


class _MemoryAsyncRedis:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def get(self, key: str):
        return self._store.get(key)

    async def set(self, key: str, value: str, **kwargs):
        if kwargs.get("nx") and key in self._store:
            return False
        self._store[key] = value
        return True

    async def delete(self, key: str):
        self._store.pop(key, None)
        return 1


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
