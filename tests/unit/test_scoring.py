from __future__ import annotations

from app.db.models import Event, EventSource, Source
from app.pipeline.scoring import ScoringService


def test_official_source_confirms_event() -> None:
    source = Source(
        id=1,
        key="binance",
        name="Binance",
        source_type="exchange_official",
        adapter="rss",
        url="https://example.com",
        canonical_url="https://example.com",
        category="listing",
        trust_score=95,
        poll_seconds=120,
        timeout_seconds=15,
        max_response_bytes=2097152,
        enabled=True,
        allow_private_networks=False,
        config={},
    )
    event = Event(
        id=1,
        event_key="listing:abc",
        title="Binance Will List ABC",
        category="listing",
        status="needs_review",
        severity="high",
        trust_score=50,
        confirmation_count=1,
        symbols=["ABC"],
        chains=[],
        entities=[],
        metadata_={},
    )
    link = EventSource(
        event_id=1, source_id=1, url="https://example.com/1", source_score=95, source=source
    )
    result = ScoringService().score(event, [link])
    assert result.status == "confirmed"
    assert result.severity == "high"
    assert result.trust_score == 95
