from __future__ import annotations

import hashlib
import json

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.field_encryption import FieldEncryptor
from app.db.models import Delivery, Event, NotificationDestination
from app.db.repositories.delivery_repo import DeliveryRepository
from app.integrations.feishu.card_renderer import render_event_card
from app.integrations.feishu.client import FeishuClient
from app.integrations.feishu.models import FeishuSendResult
from app.publishers.base import PublisherResult


class FeishuPublisher:
    channel = "feishu"

    def __init__(
        self,
        destination: NotificationDestination,
        *,
        client: FeishuClient | None = None,
        encryptor: FieldEncryptor | None = None,
        delivery_variant: str = "immediate",
        test_send: bool = False,
    ) -> None:
        self.destination = destination
        self.client = client or FeishuClient()
        self.encryptor = encryptor
        self.delivery_variant = delivery_variant
        self.test_send = test_send
        self.target = destination.key

    async def publish(self, event: Event) -> PublisherResult:
        card = render_event_card(event, dashboard_base_url=settings.public_base_url)
        if not settings.feishu_enabled or not settings.feishu_send_enabled:
            return PublisherResult(ok=True, external_id="dry-run", error=None, response_status=None)
        if not self.destination.enabled or self.destination.status != "active":
            return PublisherResult(ok=False, error="Feishu destination is not active")
        if self.test_send and self.destination.chat_id != settings.feishu_test_chat_id:
            return PublisherResult(ok=False, error="test send target is not FEISHU_TEST_CHAT_ID")
        if self.destination.provider == "feishu_app":
            if not self.destination.chat_id:
                return PublisherResult(ok=False, error="Feishu destination has no chat_id")
            result = await self.client.send_interactive_card(self.destination.chat_id, card)
        elif self.destination.provider == "feishu_webhook":
            if self.encryptor is None or not self.destination.secret_ciphertext:
                return PublisherResult(
                    ok=False,
                    error="Feishu webhook encryption is not configured",
                )
            webhook_url = self.encryptor.decrypt(self.destination.secret_ciphertext)
            result = await self.client.send_custom_webhook(
                webhook_url,
                {"msg_type": "interactive", "card": card},
                signing_secret=self.destination.config.get("signing_secret"),
            )
        else:
            return PublisherResult(ok=False, error="unsupported Feishu destination provider")
        return _publisher_result(result)

    def payload_hash(self, event: Event) -> str:
        card = render_event_card(event, dashboard_base_url=settings.public_base_url)
        raw = json.dumps(card, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def publish_feishu_once(
    session: Session,
    event: Event,
    destination: NotificationDestination,
    *,
    client: FeishuClient | None = None,
    encryptor: FieldEncryptor | None = None,
    delivery_variant: str = "immediate",
    test_send: bool = False,
) -> Delivery:
    publisher = FeishuPublisher(
        destination,
        client=client,
        encryptor=encryptor,
        delivery_variant=delivery_variant,
        test_send=test_send,
    )
    payload_hash = publisher.payload_hash(event)
    key = feishu_delivery_idempotency_key(event, destination, delivery_variant)
    repo = DeliveryRepository(session)
    delivery = repo.ensure_pending(
        event,
        channel=publisher.channel,
        target=destination.key,
        idempotency_key=key,
        destination=destination,
        delivery_variant=delivery_variant,
        payload_hash=payload_hash,
    )
    if delivery.status == "delivered":
        return delivery
    result = await publisher.publish(event)
    if result.ok:
        repo.mark_delivered(
            delivery,
            provider_message_id=result.external_id,
            response_status=result.response_status,
        )
    else:
        repo.mark_failed(
            delivery,
            _sanitize_error(result.error or "Feishu delivery failed"),
            response_status=result.response_status,
            retry_after=result.retry_after,
        )
    session.flush()
    return delivery


def feishu_delivery_idempotency_key(
    event: Event, destination: NotificationDestination, delivery_variant: str
) -> str:
    raw = f"{destination.id}:{event.id}:{event.event_key}:{delivery_variant}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _publisher_result(result: FeishuSendResult) -> PublisherResult:
    return PublisherResult(
        ok=result.ok,
        external_id=result.message_id,
        error=_sanitize_error(result.error) if result.error else None,
        response_status=result.status_code,
        retry_after=result.retry_after,
    )


def _sanitize_error(error: str) -> str:
    blocked = ("token", "secret", "webhook", "authorization", "cookie", "password")
    if any(item in error.lower() for item in blocked):
        return "Feishu delivery failed"
    return error[:300]
