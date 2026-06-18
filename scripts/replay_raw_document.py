from __future__ import annotations

import argparse

from sqlalchemy import select

from app.adapters.registry import registry
from app.core.config import SourceConfig
from app.db.models import RawDocument
from app.db.session import SessionLocal
from app.pipeline.dedupe import DedupeService
from app.schemas.raw_document import RawDocumentPayload


async def replay(raw_document_id: int) -> int:
    with SessionLocal() as session:
        raw_document = session.scalar(select(RawDocument).where(RawDocument.id == raw_document_id))
        if raw_document is None:
            raise SystemExit("raw document not found")
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
        count = 0
        for item in await adapter.parse(source_config, payload):
            dedupe.upsert_event(item, source=source, raw_document=raw_document)
            count += 1
        session.commit()
        return count


def main() -> None:
    import asyncio

    parser = argparse.ArgumentParser()
    parser.add_argument("raw_document_id", type=int)
    args = parser.parse_args()
    print(f"events_upserted={asyncio.run(replay(args.raw_document_id))}")


if __name__ == "__main__":
    main()
