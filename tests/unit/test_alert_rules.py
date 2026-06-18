from __future__ import annotations

from app.db.models import Event
from app.pipeline.alert_rules import AlertEngine


def test_confirmed_listing_alerts() -> None:
    event = Event(
        id=1,
        event_key="listing:abc",
        title="Exchange lists ABC",
        category="listing",
        status="confirmed",
        severity="high",
        trust_score=95,
        confirmation_count=1,
        symbols=["ABC"],
        chains=[],
        entities=[],
        metadata_={},
    )
    decision = AlertEngine().should_alert(event)
    assert decision.should_alert is True
    assert decision.requires_review is False
