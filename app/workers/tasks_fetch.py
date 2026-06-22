from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.registry import registry
from app.core.config import SourceConfig, load_runtime_sources
from app.core.errors import AccessDeniedError, FetchError
from app.core.time import utc_now
from app.db.models import FetchRun, Source
from app.db.repositories.raw_document_repo import RawDocumentRepository
from app.db.repositories.source_repo import SourceRepository
from app.db.session import SessionLocal
from app.fetch.client import FetchClient
from app.observability.metrics import normalized_items_total, parse_results_total
from app.observability.tracing import bind_trace_id
from app.pipeline.dedupe import DedupeService
from app.scheduler.planner import due_sources, mark_source_queued
from app.workers.celery_app import celery_app


@celery_app.task(
    name="app.workers.tasks_fetch.poll_sources",
    autoretry_for=(ConnectionError,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def poll_sources() -> dict[str, int]:
    with SessionLocal() as session:
        _sync_sources_file(session)
        count = 0
        for source in due_sources(session):
            fetch_run = mark_source_queued(session, source, trace_id=str(uuid.uuid4()))
            if fetch_run is not None:
                fetch_source.delay(source.key, fetch_run.id)
                count += 1
        session.commit()
        return {"queued": count}


@celery_app.task(
    name="app.workers.tasks_fetch.fetch_source",
    autoretry_for=(ConnectionError,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def fetch_source(source_key: str, fetch_run_id: int | None = None) -> dict[str, int | str]:
    return asyncio.run(_fetch_source(source_key, fetch_run_id=fetch_run_id))


async def _fetch_source(
    source_key: str, *, fetch_run_id: int | None = None
) -> dict[str, int | str]:
    with SessionLocal() as session:
        source = session.scalar(select(Source).where(Source.key == source_key))
        if source is None or not source.enabled:
            return {"status": "skipped", "items": 0}
        fetch_run = _get_or_create_fetch_run(session, source, fetch_run_id=fetch_run_id)
        if fetch_run.status == "success":
            return {"status": "success", "items": fetch_run.item_count}
        fetch_run.status = "running"
        bind_trace_id(fetch_run.trace_id)
        config = _source_to_config(source)
        raw_repo = RawDocumentRepository(session)
        dedupe = DedupeService(session)
        try:
            async with FetchClient(
                timeout_seconds=config.timeout_seconds,
                max_response_bytes=config.max_response_bytes,
                allow_private_networks=config.allow_private_networks,
                allow_localhost=config.allow_localhost,
                trust_env=False,
            ) as fetch_client:
                adapter = registry.get(config.adapter)
                raw_documents = await _fetch_with_conditionals(
                    adapter,
                    config,
                    fetch_client,
                    source,
                )
                item_count = 0
                for raw in raw_documents:
                    fetch_run.http_status = raw.status_code
                    _store_conditional_headers(source, raw.metadata)
                    if raw.status_code == 304:
                        fetch_run.status = "not_modified"
                        fetch_run.finished_at = utc_now()
                        fetch_run.item_count = 0
                        source.health_status = "healthy"
                        source.last_fetch_at = fetch_run.finished_at
                        source.last_http_status = 304
                        source.last_error = None
                        session.commit()
                        return {"status": "not_modified", "items": 0}
                    raw_document = raw_repo.upsert(source, raw, fetch_run_id=fetch_run.id)
                    parsed_items = await adapter.parse(config, raw)
                    parse_results_total.labels(adapter=config.adapter, outcome="success").inc()
                    normalized_items_total.labels(adapter=config.adapter).inc(len(parsed_items))
                    for item in parsed_items:
                        dedupe.upsert_event(item, source=source, raw_document=raw_document)
                        item_count += 1
                fetch_run.status = "success"
                fetch_run.finished_at = utc_now()
                fetch_run.item_count = item_count
                source.health_status = "healthy"
                source.last_fetch_at = fetch_run.finished_at
                source.last_success_at = fetch_run.finished_at
                source.last_parsed_count = item_count
                source.last_http_status = fetch_run.http_status
                source.last_error = None
                session.commit()
                return {"status": "success", "items": item_count}
        except AccessDeniedError as exc:
            SourceRepository(session).mark_access_denied(source, str(exc))
            fetch_run.status = "access_denied"
            fetch_run.finished_at = utc_now()
            fetch_run.http_status = exc.status_code
            fetch_run.error_code = exc.error_code
            fetch_run.error_message = str(exc)
            source.health_status = "access_denied"
            source.last_fetch_at = fetch_run.finished_at
            source.last_http_status = exc.status_code
            source.last_error = exc.error_code
            session.commit()
            return {"status": "access_denied", "items": 0}
        except FetchError as exc:
            fetch_run.status = "failed"
            fetch_run.finished_at = utc_now()
            fetch_run.http_status = exc.status_code
            fetch_run.error_code = exc.error_code
            fetch_run.error_message = str(exc)
            source.health_status = "degraded"
            source.last_fetch_at = fetch_run.finished_at
            source.last_http_status = exc.status_code
            source.last_error = exc.error_code
            session.commit()
            return {"status": "failed", "items": 0}
        except Exception:
            parse_results_total.labels(adapter=config.adapter, outcome="failure").inc()
            fetch_run.status = "failed"
            fetch_run.finished_at = utc_now()
            fetch_run.error_code = "unexpected_error"
            fetch_run.error_message = "unexpected task failure"
            source.health_status = "degraded"
            source.last_fetch_at = fetch_run.finished_at
            source.last_error = "unexpected_error"
            session.commit()
            raise


def _get_or_create_fetch_run(
    session: Session, source: Source, *, fetch_run_id: int | None
) -> FetchRun:
    if fetch_run_id is not None:
        fetch_run = session.scalar(
            select(FetchRun).where(FetchRun.id == fetch_run_id, FetchRun.source_id == source.id)
        )
        if fetch_run is not None:
            return fetch_run
    fetch_run = FetchRun(source_id=source.id, status="running", trace_id=str(uuid.uuid4()))
    session.add(fetch_run)
    session.flush()
    return fetch_run


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
    source: Source,
):
    try:
        return await adapter.fetch(
            config,
            fetch_client,
            etag=source.etag,
            last_modified=source.last_modified,
        )
    except TypeError as exc:
        if "unexpected keyword" not in str(exc):
            raise
        return await adapter.fetch(config, fetch_client)


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
