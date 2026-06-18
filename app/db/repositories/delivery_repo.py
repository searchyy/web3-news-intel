from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.db.models import Delivery, Event


class DeliveryRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_by_idempotency_key(self, key: str) -> Delivery | None:
        return self.session.scalar(select(Delivery).where(Delivery.idempotency_key == key))

    def ensure_pending(
        self, event: Event, *, channel: str, target: str, idempotency_key: str
    ) -> Delivery:
        delivery = self.get_by_idempotency_key(idempotency_key)
        if delivery is not None:
            return delivery
        delivery = Delivery(
            event_id=event.id,
            channel=channel,
            target=target,
            status="pending",
            idempotency_key=idempotency_key,
            attempts=0,
        )
        try:
            with self.session.begin_nested():
                self.session.add(delivery)
                self.session.flush()
            return delivery
        except IntegrityError:
            existing = self.get_by_idempotency_key(idempotency_key)
            if existing is None:
                raise
            return existing

    def mark_delivered(self, delivery: Delivery) -> None:
        delivery.status = "delivered"
        delivery.delivered_at = utc_now()
        delivery.last_error = None
        delivery.attempts += 1

    def mark_failed(self, delivery: Delivery, error: str) -> None:
        delivery.status = "failed"
        delivery.last_error = error
        delivery.attempts += 1
