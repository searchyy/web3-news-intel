from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from app.db.models import Event, Source
from app.pipeline.dedupe import DedupeService
from app.schemas.normalized_item import NormalizedItem


def test_duplicate_items_create_single_event(db_session) -> None:
    source = Source(
        key="sec",
        name="SEC",
        source_type="regulator_official",
        adapter="rss",
        url="https://sec.example/feed.xml",
        canonical_url="https://sec.example/feed.xml",
        category="regulation",
        trust_score=100,
        poll_seconds=300,
        timeout_seconds=15,
        max_response_bytes=2097152,
        enabled=True,
        allow_private_networks=False,
        config={},
    )
    db_session.add(source)
    db_session.flush()
    item = NormalizedItem(
        title="SEC Charges Example Exchange With Securities Law Violations",
        url="https://sec.example/news/1?utm_source=a",
        published_at=datetime(2026, 6, 18, tzinfo=UTC),
        source_key="sec",
        source_type="regulator_official",
        category="regulation",
    )
    service = DedupeService(db_session)
    service.upsert_event(item, source=source)
    service.upsert_event(
        item.model_copy(update={"url": "https://sec.example/news/1?utm_source=b"}), source=source
    )
    db_session.commit()
    events = list(db_session.scalars(select(Event)))
    assert len(events) == 1
    assert events[0].status == "confirmed"
