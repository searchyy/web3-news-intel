from __future__ import annotations

from app.db.models import Event
from app.publishers.base import DeliveryManager, PublisherResult


class FakePublisher:
    channel = "webhook"
    target = "https://example.com/hook"

    def __init__(self) -> None:
        self.calls = 0

    async def publish(self, event: Event) -> PublisherResult:
        self.calls += 1
        return PublisherResult(ok=True, external_id=str(event.id))


async def test_delivery_is_idempotent(db_session) -> None:
    event = Event(
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
    db_session.add(event)
    db_session.flush()
    publisher = FakePublisher()
    manager = DeliveryManager(db_session)
    first = await manager.publish_once(event, publisher)
    second = await manager.publish_once(event, publisher)
    assert first.id == second.id
    assert first.status == "delivered"
    assert publisher.calls == 1
