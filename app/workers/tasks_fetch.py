from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.registry import registry
from app.core.config import SourceConfig, load_sources
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
            ) as fetch_client:
                adapter = registry.get(config.adapter)
                raw_documents = await adapter.fetch(config, fetch_client)
                item_count = 0
                for raw in raw_documents:
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
                session.commit()
                return {"status": "success", "items": item_count}
        except AccessDeniedError as exc:
            SourceRepository(session).mark_access_denied(source, str(exc))
            fetch_run.status = "access_denied"
            fetch_run.finished_at = utc_now()
            fetch_run.http_status = exc.status_code
            fetch_run.error_code = exc.error_code
            fetch_run.error_message = str(exc)
            session.commit()
            return {"status": "access_denied", "items": 0}
        except FetchError as exc:
            fetch_run.status = "failed"
            fetch_run.finished_at = utc_now()
            fetch_run.http_status = exc.status_code
            fetch_run.error_code = exc.error_code
            fetch_run.error_message = str(exc)
            session.commit()
            return {"status": "failed", "items": 0}
        except Exception:
            parse_results_total.labels(adapter=config.adapter, outcome="failure").inc()
            fetch_run.status = "failed"
            fetch_run.finished_at = utc_now()
            fetch_run.error_code = "unexpected_error"
            fetch_run.error_message = "unexpected task failure"
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
        source_type=source.source_type,
        adapter=source.adapter,
        url=source.url,
        canonical_url=source.canonical_url,
        category=source.category,
        language=source.language,
        trust_score=source.trust_score,
        poll_seconds=source.poll_seconds,
        timeout_seconds=source.timeout_seconds,
        max_response_bytes=source.max_response_bytes,
        enabled=source.enabled,
        allow_private_networks=source.allow_private_networks,
        allow_localhost=source.allow_localhost,
        config=source.config,
    )


def _sync_sources_file(session) -> None:
    try:
        sources_file = load_sources()
    except FileNotFoundError:
        return
    repo = SourceRepository(session)
    for source in sources_file.sources.values():
        repo.upsert_from_config(source)
    session.commit()
