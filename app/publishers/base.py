from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.orm import Session

from app.db.models import Delivery, Event
from app.db.repositories.delivery_repo import DeliveryRepository
from app.observability.metrics import delivery_latency_seconds, publisher_results_total


@dataclass(slots=True)
class PublisherResult:
    ok: bool
    external_id: str | None = None
    error: str | None = None


class Publisher(Protocol):
    channel: str
    target: str

    async def publish(self, event: Event) -> PublisherResult: ...


def delivery_idempotency_key(event: Event, channel: str, target: str) -> str:
    raw = f"{event.id}:{event.event_key}:{channel}:{target}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def format_event_message(event: Event) -> str:
    prefix = "Reported" if event.status == "needs_review" else "Confirmed"
    symbols = f" [{', '.join(event.symbols)}]" if event.symbols else ""
    url = f"\n{event.primary_url}" if event.primary_url else ""
    summary = f"\n{event.summary[:300]}" if event.summary else ""
    return f"{prefix}: {event.title}{symbols}\nSeverity: {event.severity}{summary}{url}"


class DeliveryManager:
    def __init__(self, session: Session):
        self.session = session
        self.repo = DeliveryRepository(session)

    async def publish_once(self, event: Event, publisher: Publisher) -> Delivery:
        key = delivery_idempotency_key(event, publisher.channel, publisher.target)
        delivery = self.repo.ensure_pending(
            event, channel=publisher.channel, target=publisher.target, idempotency_key=key
        )
        if delivery.status == "delivered":
            return delivery
        import time

        started = time.perf_counter()
        result = await publisher.publish(event)
        if result.ok:
            self.repo.mark_delivered(delivery)
            publisher_results_total.labels(channel=publisher.channel, outcome="success").inc()
            delivery_latency_seconds.labels(channel=publisher.channel).observe(
                time.perf_counter() - started
            )
        else:
            self.repo.mark_failed(delivery, result.error or "publisher failed")
            publisher_results_total.labels(channel=publisher.channel, outcome="failure").inc()
        self.session.flush()
        return delivery
