from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import RawDocument, Source
from app.schemas.raw_document import RawDocumentPayload


class RawDocumentRepository:
    def __init__(self, session: Session):
        self.session = session

    def upsert(
        self, source: Source, payload: RawDocumentPayload, *, fetch_run_id: int | None = None
    ) -> RawDocument:
        existing = self.session.scalar(
            select(RawDocument).where(
                RawDocument.source_id == source.id,
                RawDocument.body_hash == payload.body_hash,
            )
        )
        if existing is not None:
            return existing
        document = RawDocument(
            source_id=source.id,
            fetch_run_id=fetch_run_id,
            url=payload.url,
            canonical_url=payload.canonical_url,
            content_type=payload.content_type,
            status_code=payload.status_code,
            body_hash=payload.body_hash,
            body=payload.body,
            metadata_=payload.metadata,
            fetched_at=payload.fetched_at,
        )
        self.session.add(document)
        self.session.flush()
        return document
