from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from app.db.models import Event, EventSource, Source
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

def test_same_source_url_merges_when_event_key_changes(db_session) -> None:
    source = Source(
        key="blockbeats_newsflash",
        name="BlockBeats Newsflash",
        source_type="chinese_media",
        adapter="media_html",
        url="https://m.theblockbeats.info/newsflash",
        canonical_url="https://m.theblockbeats.info/newsflash",
        category="newsflash",
        trust_score=72,
        poll_seconds=120,
        timeout_seconds=15,
        max_response_bytes=2097152,
        enabled=True,
        allow_private_networks=False,
        config={},
    )
    db_session.add(source)
    db_session.flush()
    service = DedupeService(db_session)
    first = NormalizedItem(
        title="01:01 Fed rate probability update",
        url="https://m.theblockbeats.info/flash/352778",
        published_at=None,
        source_key=source.key,
        source_type=source.source_type,
        category="newsflash",
    )
    second_published = datetime(2026, 6, 23, 17, 1, tzinfo=UTC)
    second = first.model_copy(
        update={
            "title": "Fed rate probability update",
            "published_at": second_published,
        }
    )

    first_event = service.upsert_event(first, source=source)
    second_event = service.upsert_event(second, source=source)
    db_session.commit()

    events = list(db_session.scalars(select(Event)))
    event_sources = list(db_session.scalars(select(EventSource)))
    assert first_event.id == second_event.id
    assert len(events) == 1
    assert len(event_sources) == 1
    assert events[0].published_at == second_published
