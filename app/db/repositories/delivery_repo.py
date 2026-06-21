from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.db.models import Delivery, Event, NotificationDestination


class DeliveryRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_by_idempotency_key(self, key: str) -> Delivery | None:
        return self.session.scalar(select(Delivery).where(Delivery.idempotency_key == key))

    def ensure_pending(
        self,
        event: Event,
        *,
        channel: str,
        target: str,
        idempotency_key: str,
        destination: NotificationDestination | None = None,
        delivery_variant: str = "immediate",
        payload_hash: str | None = None,
    ) -> Delivery:
        delivery = self.get_by_idempotency_key(idempotency_key)
        if delivery is not None:
            return delivery
        delivery = Delivery(
            event_id=event.id,
            destination_id=destination.id if destination else None,
            channel=channel,
            target=target,
            status="pending",
            idempotency_key=idempotency_key,
            delivery_variant=delivery_variant,
            payload_hash=payload_hash,
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

    def mark_delivered(
        self,
        delivery: Delivery,
        *,
        provider_message_id: str | None = None,
        response_status: int | None = None,
    ) -> None:
        delivery.status = "delivered"
        delivery.delivered_at = utc_now()
        delivery.provider_message_id = provider_message_id
        delivery.response_status = response_status
        delivery.last_error = None
        delivery.attempts += 1

    def claim_sending(self, delivery: Delivery) -> bool:
        result = self.session.execute(
            update(Delivery)
            .where(Delivery.id == delivery.id, Delivery.status.in_(["pending", "failed"]))
            .values(status="sending")
        )
        claimed = result.rowcount == 1
        if claimed:
            self.session.flush()
            self.session.refresh(delivery)
        return claimed

    def mark_failed(
        self,
        delivery: Delivery,
        error: str,
        *,
        response_status: int | None = None,
        retry_after: int | None = None,
    ) -> None:
        delivery.status = "failed"
        delivery.last_error = error
        delivery.response_status = response_status
        delivery.retry_after = retry_after
        delivery.attempts += 1
