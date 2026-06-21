from __future__ import annotations

import asyncio
import json

from sqlalchemy import select

from app.core.config import settings
from app.core.time import utc_now
from app.db.models import Event, NotificationRule
from app.db.repositories.notification_repo import NotificationRepository
from app.db.session import SessionLocal
from app.pipeline.destination_router import DestinationRouter
from app.publishers.feishu import publish_feishu_once


def main() -> int:
    if settings.feishu_send_enabled:
        raise RuntimeError("compose Feishu E2E must not perform real sends")
    with SessionLocal() as session:
        repo = NotificationRepository(session)
        destination = repo.upsert_feishu_group(
            chat_id="oc_compose_fixture",
            chat_name="Compose fixture",
        )
        repo.approve(destination)
        if not destination.rules:
            rule = NotificationRule(
                destination_id=destination.id,
                name="compose immediate",
                enabled=True,
                minimum_severity="low",
                categories=[],
                sources=[],
                symbols=[],
                chains=[],
                delivery_mode="immediate",
                timezone="UTC",
                maximum_messages_per_hour=30,
            )
            session.add(rule)
            session.flush()
        event = session.scalar(select(Event).where(Event.event_key == "compose-feishu:e2e"))
        if event is None:
            event = Event(
                event_key="compose-feishu:e2e",
                title="Compose Feishu mocked E2E",
                summary="Dry-run Feishu delivery from Compose acceptance.",
                category="system",
                status="confirmed",
                severity="low",
                language="en",
                primary_url=None,
                published_at=utc_now(),
                first_seen_at=utc_now(),
                last_seen_at=utc_now(),
                trust_score=100,
                confirmation_count=1,
                symbols=[],
                chains=[],
                entities=[],
                metadata_={"compose": True},
            )
            session.add(event)
            session.flush()
        decision = DestinationRouter(session).should_route(event, destination)
        if not decision.should_send:
            raise RuntimeError(f"expected route decision to send, got {decision.reason}")
        first = asyncio.run(publish_feishu_once(session, event, destination))
        second = asyncio.run(publish_feishu_once(session, event, destination))
        session.commit()
        payload = {
            "destination_id": str(destination.id),
            "event_id": event.id,
            "first_delivery_id": first.id,
            "second_delivery_id": second.id,
            "duplicate_delivery_created": first.id != second.id,
            "status": second.status,
        }
        print(json.dumps(payload, sort_keys=True))
        if first.id != second.id or second.status != "delivered":
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
