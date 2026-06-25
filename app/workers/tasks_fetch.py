from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.adapters.registry import registry
from app.core.config import SourceConfig, load_runtime_sources, settings
from app.core.errors import AccessDeniedError, FetchError
from app.core.time import ensure_utc, utc_now
from app.db.models import FetchRun, Source
from app.db.repositories.raw_document_repo import RawDocumentRepository
from app.db.repositories.source_repo import SourceRepository
from app.db.session import SessionLocal
from app.fetch.client import FetchClient
from app.observability.metrics import normalized_items_total, parse_results_total
from app.observability.tracing import bind_trace_id
from app.pipeline.dedupe import DedupeService
from app.scheduler.planner import ACTIVE_FETCH_STATUSES, begin_polling_write_lock, queue_due_sources
from app.workers.celery_app import CELERY_FETCH_PRIORITY, CELERY_FETCH_QUEUE, celery_app


@dataclass(frozen=True, slots=True)
class _FetchClaim:
    source_id: int
    source_key: str
    fetch_run_id: int
    trace_id: str
    config: SourceConfig
    etag: str | None
    last_modified: str | None


@celery_app.task(
    name="app.workers.tasks_fetch.poll_sources",
    queue=CELERY_FETCH_QUEUE,
    priority=CELERY_FETCH_PRIORITY,
    autoretry_for=(ConnectionError,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def poll_sources() -> dict[str, int]:
    with SessionLocal() as session:
        _sync_sources_file(session)
        queued = queue_due_sources(session, trace_id_factory=lambda: str(uuid.uuid4()))
        session.commit()
    enqueued = 0
    for fetch_run in queued:
        if enqueue_fetch_run(fetch_run.source_key, fetch_run.fetch_run_id):
            enqueued += 1
    return {"queued": enqueued}


@celery_app.task(
    name="app.workers.tasks_fetch.fetch_source",
    queue=CELERY_FETCH_QUEUE,
    priority=CELERY_FETCH_PRIORITY,
    autoretry_for=(ConnectionError,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def fetch_source(
    source_key: str, fetch_run_id: int | None = None, force: bool = False
) -> dict[str, int | str]:
    return asyncio.run(_fetch_source(source_key, fetch_run_id=fetch_run_id, force=force))


async def _fetch_source(
    source_key: str, *, fetch_run_id: int | None = None, force: bool = False
) -> dict[str, int | str]:
    claim = _claim_fetch_run(source_key, fetch_run_id=fetch_run_id, force=force)
    if isinstance(claim, dict):
        return claim

    bind_trace_id(claim.trace_id)
    config = claim.config
    try:
        async with FetchClient(
            timeout_seconds=config.timeout_seconds,
            max_response_bytes=config.max_response_bytes,
            allow_private_networks=config.allow_private_networks,
            allow_localhost=config.allow_localhost,
            validate_dns_rebinding=_source_validate_dns_rebinding(config),
            trust_env=settings.http_trust_env,
        ) as fetch_client:
            adapter = registry.get(config.adapter)
            raw_documents = await _fetch_with_conditionals(
                adapter,
                config,
                fetch_client,
                etag=claim.etag,
                last_modified=claim.last_modified,
            )
    except AccessDeniedError as exc:
        return _finish_access_denied(claim, exc)
    except FetchError as exc:
        return _finish_failed_fetch(
            claim,
            http_status=exc.status_code,
            error_code=exc.error_code,
            error_message=str(exc),
        )
    except Exception:
        _finish_failed_fetch(
            claim,
            http_status=None,
            error_code="unexpected_error",
            error_message="unexpected task failure",
        )
        raise

    try:
        return await _store_fetch_result(claim, adapter, config, raw_documents)
    except Exception:
        parse_results_total.labels(adapter=config.adapter, outcome="failure").inc()
        _finish_failed_fetch(
            claim,
            http_status=None,
            error_code="unexpected_error",
            error_message="unexpected task failure",
        )
        raise


def _claim_fetch_run(
    source_key: str, *, fetch_run_id: int | None, force: bool = False
) -> _FetchClaim | dict[str, int | str]:
    with SessionLocal() as session:
        if not begin_polling_write_lock(session):
            return {"status": "skipped", "items": 0, "reason": "active_fetch_lock_busy"}

        source = _lock_source_for_fetch(session, source_key)
        if source is None:
            _mark_run_skipped_by_id(session, fetch_run_id, error_code="source_missing")
            session.commit()
            return {"status": "skipped", "items": 0}

        if not source.enabled:
            _mark_run_skipped_by_id(session, fetch_run_id, error_code="source_disabled")
            session.commit()
            return {"status": "skipped", "items": 0}

        now = utc_now()
        circuit_open_until = ensure_utc(source.circuit_open_until)
        if circuit_open_until is not None and circuit_open_until > now and not force:
            _mark_run_skipped_by_id(session, fetch_run_id, error_code="circuit_open")
            session.commit()
            return {"status": "circuit_open", "items": 0}
        if force:
            source.circuit_open_until = None

        fetch_run = _get_or_create_fetch_run(session, source, fetch_run_id=fetch_run_id)
        if fetch_run is None:
            session.commit()
            return {"status": "skipped", "items": 0}
        if fetch_run.status == "success":
            session.commit()
            return {"status": "success", "items": fetch_run.item_count}
        if fetch_run.status != "queued":
            session.commit()
            return {"status": fetch_run.status, "items": fetch_run.item_count}

        conflict = _active_fetch_conflict(session, source.id, fetch_run.id)
        if conflict is not None:
            _mark_run_skipped(fetch_run, error_code="active_fetch_exists")
            session.commit()
            return {"status": "skipped", "items": 0}

        _skip_later_queued_runs(session, source.id, fetch_run.id)
        fetch_run.status = "running"
        fetch_run.worker_started_at = utc_now()
        config = _source_to_config(source)
        claim = _FetchClaim(
            source_id=source.id,
            source_key=source.key,
            fetch_run_id=fetch_run.id,
            trace_id=fetch_run.trace_id,
            config=config,
            etag=None if force else source.etag,
            last_modified=None if force else source.last_modified,
        )
        session.commit()
        return claim


def _lock_source_for_fetch(session: Session, source_key: str) -> Source | None:
    stmt = select(Source).where(Source.key == source_key)
    if session.get_bind().dialect.name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)
    return session.scalar(stmt)


def _get_or_create_fetch_run(
    session: Session, source: Source, *, fetch_run_id: int | None
) -> FetchRun | None:
    if fetch_run_id is not None:
        fetch_run = session.scalar(
            select(FetchRun).where(FetchRun.id == fetch_run_id, FetchRun.source_id == source.id)
        )
        return fetch_run
    if (
        session.scalar(
            select(FetchRun.id)
            .where(
                FetchRun.source_id == source.id,
                FetchRun.status.in_(ACTIVE_FETCH_STATUSES),
            )
            .limit(1)
        )
        is not None
    ):
        return None
    now = utc_now()
    fetch_run = FetchRun(
        source_id=source.id,
        status="queued",
        trace_id=str(uuid.uuid4()),
        queued_at=now,
        started_at=now,
    )
    session.add(fetch_run)
    session.flush()
    return fetch_run


def _active_fetch_conflict(
    session: Session, source_id: int, fetch_run_id: int
) -> FetchRun | None:
    return session.scalar(
        select(FetchRun)
        .where(
            FetchRun.source_id == source_id,
            FetchRun.id != fetch_run_id,
            or_(
                FetchRun.status == "running",
                and_(FetchRun.status == "queued", FetchRun.id < fetch_run_id),
            ),
        )
        .order_by(FetchRun.id)
        .limit(1)
    )


def _skip_later_queued_runs(session: Session, source_id: int, fetch_run_id: int) -> None:
    for duplicate in session.scalars(
        select(FetchRun).where(
            FetchRun.source_id == source_id,
            FetchRun.status == "queued",
            FetchRun.id > fetch_run_id,
        )
    ):
        _mark_run_skipped(duplicate, error_code="duplicate_active_fetch")


def _mark_run_skipped_by_id(
    session: Session, fetch_run_id: int | None, *, error_code: str
) -> None:
    if fetch_run_id is None:
        return
    fetch_run = session.get(FetchRun, fetch_run_id)
    if fetch_run is None or fetch_run.status not in ACTIVE_FETCH_STATUSES:
        return
    _mark_run_skipped(fetch_run, error_code=error_code)


def _mark_run_skipped(fetch_run: FetchRun, *, error_code: str) -> None:
    fetch_run.status = "skipped"
    fetch_run.finished_at = utc_now()
    fetch_run.error_code = error_code
    fetch_run.error_message = error_code


def _record_enqueued_task(fetch_run_id: int, task_id: str | None) -> None:
    if not task_id:
        return
    with SessionLocal() as session:
        fetch_run = session.get(FetchRun, fetch_run_id)
        if fetch_run is None:
            return
        fetch_run.task_id = task_id
        session.commit()


def enqueue_fetch_run(source_key: str, fetch_run_id: int, *, force: bool = False) -> bool:
    try:
        if force:
            result = fetch_source.delay(source_key, fetch_run_id, force=True)
        else:
            result = fetch_source.delay(source_key, fetch_run_id)
    except Exception:
        _mark_enqueue_failed(fetch_run_id)
        return False
    _record_enqueued_task(fetch_run_id, getattr(result, "id", None))
    return True


def _enqueue_event_pipeline(event_ids: set[int]) -> None:
    if not event_ids:
        return
    if not (
        settings.ai_enabled
        or settings.ai_auto_process_enabled
        or settings.feishu_enabled
        or settings.feishu_send_enabled
    ):
        return
    from app.workers.tasks_publish import process_event_pipeline

    for event_id in sorted(event_ids):
        try:
            process_event_pipeline.delay(event_id)
        except Exception:
            continue


def _mark_enqueue_failed(fetch_run_id: int) -> None:
    with SessionLocal() as session:
        fetch_run = session.get(FetchRun, fetch_run_id)
        if fetch_run is None or fetch_run.status != "queued":
            return
        source = session.get(Source, fetch_run.source_id)
        finished_at = utc_now()
        fetch_run.status = "failed"
        fetch_run.finished_at = finished_at
        fetch_run.error_code = "enqueue_failed"
        fetch_run.error_message = "enqueue_failed"
        fetch_run.retry_after_until = finished_at + timedelta(seconds=60)
        if source is not None:
            source.health_status = "degraded"
            source.last_fetch_at = finished_at
            source.last_error = "enqueue_failed"
            source.consecutive_failures = (source.consecutive_failures or 0) + 1
            source.circuit_open_until = fetch_run.retry_after_until
        session.commit()


def _source_validate_dns_rebinding(config: SourceConfig) -> bool | None:
    value = config.config.get("validate_dns_rebinding")
    if value is None:
        return None
    return bool(value)


def _source_to_config(source: Source) -> SourceConfig:
    return SourceConfig(
        key=source.key,
        name=source.name,
        display_name_zh=source.display_name_zh,
        source_group=source.source_group,
        source_type=source.source_type,
        adapter=source.adapter,
        url=source.url,
        canonical_url=source.canonical_url,
        category=source.category,
        language=source.language,
        official=source.official,
        trust_score=source.trust_score,
        poll_seconds=source.poll_seconds,
        timeout_seconds=source.timeout_seconds,
        max_response_bytes=source.max_response_bytes,
        max_items_per_fetch=source.max_items_per_fetch,
        enabled=source.enabled,
        allow_private_networks=source.allow_private_networks,
        allow_localhost=source.allow_localhost,
        ranking_provider=source.ranking_provider,
        ranking_position=source.ranking_position,
        ranking_snapshot_at=source.ranking_snapshot_at,
        parser_version=source.parser_version,
        supported_categories=source.supported_categories,
        health_status=source.health_status,
        live_canary_status=source.live_canary_status,
        last_canary_at=source.last_canary_at,
        last_canary_error=source.last_canary_error,
        config=source.config,
    )


async def _fetch_with_conditionals(
    adapter,
    config: SourceConfig,
    fetch_client: FetchClient,
    *,
    etag: str | None,
    last_modified: str | None,
):
    try:
        return await adapter.fetch(
            config,
            fetch_client,
            etag=etag,
            last_modified=last_modified,
        )
    except TypeError as exc:
        if "unexpected keyword" not in str(exc):
            raise
        return await adapter.fetch(config, fetch_client)


async def _store_fetch_result(
    claim: _FetchClaim,
    adapter,
    config: SourceConfig,
    raw_documents,
) -> dict[str, int | str]:
    with SessionLocal() as session:
        source = session.get(Source, claim.source_id)
        fetch_run = session.get(FetchRun, claim.fetch_run_id)
        if source is None or fetch_run is None:
            return {"status": "skipped", "items": 0}
        raw_repo = RawDocumentRepository(session)
        dedupe = DedupeService(session)
        item_count = 0
        event_ids: set[int] = set()
        for raw in raw_documents:
            fetch_run.http_status = raw.status_code
            _store_conditional_headers(source, raw.metadata)
            if raw.status_code == 304:
                finished_at = utc_now()
                fetch_run.status = "not_modified"
                fetch_run.finished_at = finished_at
                fetch_run.item_count = 0
                _record_source_success(
                    source,
                    finished_at=finished_at,
                    http_status=304,
                    parsed_count=None,
                )
                session.commit()
                return {"status": "not_modified", "items": 0}
            raw_document = raw_repo.upsert(source, raw, fetch_run_id=fetch_run.id)
            parsed_items = await adapter.parse(config, raw)
            parse_results_total.labels(adapter=config.adapter, outcome="success").inc()
            normalized_items_total.labels(adapter=config.adapter).inc(len(parsed_items))
            for item in parsed_items:
                event = dedupe.upsert_event(item, source=source, raw_document=raw_document)
                if event.id is not None:
                    event_ids.add(int(event.id))
                item_count += 1
        finished_at = utc_now()
        fetch_run.status = "success"
        fetch_run.finished_at = finished_at
        fetch_run.item_count = item_count
        _record_source_success(
            source,
            finished_at=finished_at,
            http_status=fetch_run.http_status,
            parsed_count=item_count,
        )
        session.commit()
        _enqueue_event_pipeline(event_ids)
        return {"status": "success", "items": item_count}


def _finish_access_denied(claim: _FetchClaim, exc: AccessDeniedError) -> dict[str, int | str]:
    with SessionLocal() as session:
        source = session.get(Source, claim.source_id)
        fetch_run = session.get(FetchRun, claim.fetch_run_id)
        if source is None or fetch_run is None:
            return {"status": "skipped", "items": 0}
        finished_at = utc_now()
        SourceRepository(session).mark_access_denied(source, str(exc))
        fetch_run.status = "access_denied"
        fetch_run.finished_at = finished_at
        fetch_run.http_status = exc.status_code
        fetch_run.error_code = exc.error_code
        fetch_run.error_message = str(exc)
        source.health_status = "access_denied"
        source.last_fetch_at = finished_at
        source.last_http_status = exc.status_code
        source.last_error = exc.error_code
        source.consecutive_failures = (source.consecutive_failures or 0) + 1
        source.circuit_open_until = None
        session.commit()
        return {"status": "access_denied", "items": 0}


def _finish_failed_fetch(
    claim: _FetchClaim,
    *,
    http_status: int | None,
    error_code: str,
    error_message: str,
) -> dict[str, int | str]:
    with SessionLocal() as session:
        source = session.get(Source, claim.source_id)
        fetch_run = session.get(FetchRun, claim.fetch_run_id)
        if source is None or fetch_run is None:
            return {"status": "skipped", "items": 0}
        finished_at = utc_now()
        fetch_run.status = "failed"
        fetch_run.finished_at = finished_at
        fetch_run.http_status = http_status
        fetch_run.error_code = error_code
        fetch_run.error_message = error_message
        _record_source_failure(
            source,
            finished_at=finished_at,
            http_status=http_status,
            error_code=error_code,
        )
        fetch_run.retry_after_until = source.circuit_open_until
        session.commit()
        return {"status": "failed", "items": 0}


def _record_source_success(
    source: Source,
    *,
    finished_at,
    http_status: int | None,
    parsed_count: int | None,
) -> None:
    source.health_status = "healthy"
    source.last_fetch_at = finished_at
    source.last_success_at = finished_at
    source.last_http_status = http_status
    source.last_error = None
    source.consecutive_failures = 0
    source.circuit_open_until = None
    source.access_denied_at = None
    source.access_denied_reason = None
    if parsed_count is not None:
        source.last_parsed_count = parsed_count


def _record_source_failure(
    source: Source,
    *,
    finished_at,
    http_status: int | None,
    error_code: str,
) -> None:
    source.consecutive_failures = (source.consecutive_failures or 0) + 1
    source.circuit_open_until = finished_at + timedelta(
        seconds=_circuit_delay_seconds(source.consecutive_failures, http_status=http_status)
    )
    source.health_status = "degraded"
    source.last_fetch_at = finished_at
    source.last_http_status = http_status
    source.last_error = error_code


def _circuit_delay_seconds(consecutive_failures: int, *, http_status: int | None) -> int:
    base_seconds = 120 if http_status == 429 else 60
    return min(3600, base_seconds * (2 ** max(0, consecutive_failures - 1)))


def _store_conditional_headers(source: Source, metadata: dict[str, object]) -> None:
    etag = metadata.get("etag")
    last_modified = metadata.get("last_modified")
    if isinstance(etag, str) and etag:
        source.etag = etag
    if isinstance(last_modified, str) and last_modified:
        source.last_modified = last_modified


def _sync_sources_file(session) -> None:
    try:
        sources = load_runtime_sources()
    except FileNotFoundError:
        return
    repo = SourceRepository(session)
    for source in sources.values():
        repo.upsert_from_config(source)
    session.commit()
