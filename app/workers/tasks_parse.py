from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.adapters.registry import registry
from app.core.config import SourceConfig, settings
from app.db.models import RawDocument
from app.db.session import SessionLocal
from app.observability.metrics import normalized_items_total, parse_results_total
from app.pipeline.dedupe import DedupeService
from app.schemas.raw_document import RawDocumentPayload
from app.workers.celery_app import celery_app


@celery_app.task(
    name="app.workers.tasks_parse.parse_raw_document",
    autoretry_for=(ConnectionError,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def parse_raw_document(raw_document_id: int) -> dict[str, int | str]:
    return asyncio.run(_parse_raw_document(raw_document_id))


async def _parse_raw_document(raw_document_id: int) -> dict[str, int | str]:
    with SessionLocal() as session:
        raw_document = session.scalar(select(RawDocument).where(RawDocument.id == raw_document_id))
        if raw_document is None:
            return {"status": "not_found", "items": 0}
        source = raw_document.source
        source_config = SourceConfig(
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
        payload = RawDocumentPayload(
            source_key=source.key,
            url=raw_document.url,
            canonical_url=raw_document.canonical_url,
            content_type=raw_document.content_type,
            status_code=raw_document.status_code,
            body_hash=raw_document.body_hash,
            body=raw_document.body,
            metadata=raw_document.metadata_,
            fetched_at=raw_document.fetched_at,
        )
        adapter = registry.get(source.adapter)
        dedupe = DedupeService(session)
        item_count = 0
        parsed_items = await adapter.parse(source_config, payload)
        parse_results_total.labels(adapter=source.adapter, outcome="success").inc()
        normalized_items_total.labels(adapter=source.adapter).inc(len(parsed_items))
        event_ids: set[int] = set()
        for item in parsed_items:
            event = dedupe.upsert_event(item, source=source, raw_document=raw_document)
            if event.id is not None:
                event_ids.add(int(event.id))
            item_count += 1
        session.commit()
        _enqueue_event_pipeline(event_ids)
        return {"status": "success", "items": item_count}


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
