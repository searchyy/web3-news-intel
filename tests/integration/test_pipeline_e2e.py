from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from sqlalchemy import select

from app.adapters.rss import RSSAdapter
from app.core.config import SourceConfig
from app.db.models import Event, Source
from app.pipeline.dedupe import DedupeService
from app.schemas.raw_document import RawDocumentPayload


async def test_duplicate_rss_items_do_not_create_duplicate_events(db_session) -> None:
    body = Path("tests/fixtures/rss_sec.xml").read_text(encoding="utf-8")
    source_model = Source(
        key="sec_press",
        name="SEC Press",
        source_type="regulator_official",
        adapter="rss",
        url="https://sec.example/rss",
        canonical_url="https://sec.example/rss",
        category="regulation",
        trust_score=100,
        poll_seconds=300,
        timeout_seconds=15,
        max_response_bytes=2097152,
        enabled=True,
        allow_private_networks=False,
        config={},
    )
    db_session.add(source_model)
    db_session.flush()
    source = SourceConfig(
        key=source_model.key,
        name=source_model.name,
        source_type=source_model.source_type,
        adapter="rss",
        url=source_model.url,
        canonical_url=source_model.canonical_url,
        category=source_model.category,
        trust_score=source_model.trust_score,
        timeout_seconds=source_model.timeout_seconds,
        max_response_bytes=source_model.max_response_bytes,
    )
    raw = RawDocumentPayload(
        source_key=source.key,
        url=source.url,
        body_hash=sha256(body.encode()).hexdigest(),
        body=body,
    )
    items = await RSSAdapter().parse(source, raw)
    dedupe = DedupeService(db_session)
    for item in items:
        dedupe.upsert_event(item, source=source_model)
    db_session.commit()
    events = list(db_session.scalars(select(Event)))
    assert len(items) == 2
    assert len(events) == 1
    assert events[0].status == "confirmed"
    assert events[0].severity == "critical"
