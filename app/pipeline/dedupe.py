from __future__ import annotations

import hashlib
from datetime import UTC

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.time import ensure_utc, utc_now
from app.db.models import Event, EventSource, RawDocument, Source
from app.observability.metrics import event_upserts_total
from app.pipeline.normalize import normalize_item, title_fingerprint
from app.pipeline.scoring import ScoringService, source_base_score
from app.pipeline.severity import severity_for_category
from app.schemas.normalized_item import NormalizedItem


class DedupeService:
    def __init__(self, session: Session | None = None):
        self.session = session
        self.scoring = ScoringService()

    def build_event_key(self, item: NormalizedItem) -> str:
        normalized = normalize_item(item)
        published = normalized.published_at or utc_now()
        bucket = published.astimezone(UTC).strftime("%Y-%m-%d")
        symbols = ",".join(normalized.symbols[:5])
        title_key = title_fingerprint(normalized.title)
        raw_key = "|".join([normalized.category, bucket, symbols, title_key])
        digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:32]
        return f"{normalized.category}:{digest}"

    def upsert_event(
        self,
        item: NormalizedItem,
        *,
        source: Source | None = None,
        raw_document: RawDocument | None = None,
    ) -> Event:
        if self.session is None:
            raise RuntimeError("DedupeService.upsert_event requires a database session")
        normalized = normalize_item(item)
        event_key = self.build_event_key(normalized)
        event = self.session.scalar(select(Event).where(Event.event_key == event_key))
        source_score = (
            source.trust_score if source is not None else source_base_score(normalized.source_type)
        )
        now = utc_now()
        if event is None:
            event = self._create_or_get_event(event_key, normalized, source_score, now)
            event_upserts_total.labels(outcome="created").inc()
        else:
            event_upserts_total.labels(outcome="merged").inc()
            event.last_seen_at = now
            event.symbols = sorted(set(event.symbols or []) | set(normalized.symbols))
            event.chains = sorted(set(event.chains or []) | set(normalized.chains))
            event.entities = sorted(set(event.entities or []) | set(normalized.entities))
            if normalized.summary and not event.summary:
                event.summary = normalized.summary
            current_published_at = ensure_utc(event.published_at)
            if normalized.published_at and (
                current_published_at is None or normalized.published_at < current_published_at
            ):
                event.published_at = normalized.published_at

        if source is not None:
            self._create_event_source_if_missing(
                event,
                source,
                normalized,
                raw_document=raw_document,
                source_score=source_score,
            )

        self._refresh_score(event)
        return event

    def _refresh_score(self, event: Event) -> None:
        event_sources = list(
            self.session.scalars(
                select(EventSource).where(EventSource.event_id == event.id).order_by(EventSource.id)
            )
        )
        result = self.scoring.score(event, event_sources)
        event.trust_score = result.trust_score
        event.status = result.status
        event.severity = result.severity
        event.confirmation_count = result.confirmation_count
        metadata = dict(event.metadata_ or {})
        metadata["score_reasons"] = result.reasons
        event.metadata_ = metadata

    def _create_or_get_event(
        self,
        event_key: str,
        normalized: NormalizedItem,
        source_score: int,
        now,
    ) -> Event:
        event = Event(
            event_key=event_key,
            title=normalized.title,
            summary=normalized.summary,
            category=normalized.category,
            status="needs_review",
            severity=severity_for_category(normalized.category),
            language=normalized.language,
            primary_url=normalized.canonical_url or normalized.url,
            published_at=normalized.published_at,
            first_seen_at=now,
            last_seen_at=now,
            trust_score=source_score,
            confirmation_count=1,
            symbols=normalized.symbols,
            chains=normalized.chains,
            entities=normalized.entities,
            metadata_={"source_key": normalized.source_key},
        )
        try:
            with self.session.begin_nested():
                self.session.add(event)
                self.session.flush()
            return event
        except IntegrityError:
            existing = self.session.scalar(select(Event).where(Event.event_key == event_key))
            if existing is None:
                raise
            return existing

    def _create_event_source_if_missing(
        self,
        event: Event,
        source: Source,
        normalized: NormalizedItem,
        *,
        raw_document: RawDocument | None,
        source_score: int,
    ) -> None:
        existing_link = self.session.scalar(
            select(EventSource).where(
                EventSource.event_id == event.id,
                EventSource.source_id == source.id,
                EventSource.url == normalized.url,
            )
        )
        if existing_link is not None:
            return
        try:
            with self.session.begin_nested():
                self.session.add(
                    EventSource(
                        event_id=event.id,
                        source_id=source.id,
                        raw_document_id=raw_document.id if raw_document is not None else None,
                        url=normalized.url,
                        title=normalized.title,
                        published_at=normalized.published_at,
                        source_score=source_score,
                        source=source,
                    )
                )
                self.session.flush()
        except IntegrityError:
            return
