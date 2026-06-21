from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.field_encryption import FieldEncryptor
from app.db.models import (
    Delivery,
    Event,
    FeishuCallbackReceipt,
    NotificationRule,
)
from app.db.repositories.notification_repo import NotificationRepository
from app.publishers.feishu import feishu_delivery_idempotency_key

pytestmark = pytest.mark.postgres


def test_notification_destination_rule_and_delivery_idempotency(postgres_session) -> None:
    encryptor = FieldEncryptor(FieldEncryptor.generate_key())
    destination = NotificationRepository(postgres_session).create_webhook_destination(
        key="feishu-webhook-it",
        name="Feishu integration",
        webhook_url="write-only-webhook-placeholder",
        encryptor=encryptor,
        config={"mode": "test"},
    )
    rule = NotificationRule(
        destination_id=destination.id,
        name="critical only",
        minimum_severity="critical",
        categories=["security"],
        sources=[],
        symbols=["ETH"],
        chains=[],
        delivery_mode="immediate",
        timezone="UTC",
        maximum_messages_per_hour=10,
    )
    event = Event(
        event_key="postgres-notification:test",
        title="Notification test",
        category="security",
        status="confirmed",
        severity="critical",
        trust_score=90,
        confirmation_count=1,
        symbols=["ETH"],
        chains=[],
        entities=[],
        metadata_={"jsonb": True},
        first_seen_at=datetime.now(UTC),
        last_seen_at=datetime.now(UTC),
    )
    postgres_session.add_all([rule, event])
    postgres_session.flush()
    key = feishu_delivery_idempotency_key(event, destination, "immediate")
    postgres_session.add(
        Delivery(
            event_id=event.id,
            destination_id=destination.id,
            channel="feishu",
            target=destination.key,
            status="delivered",
            idempotency_key=key,
            delivery_variant="immediate",
            attempts=1,
        )
    )
    postgres_session.flush()
    assert destination.secret_ciphertext
    assert encryptor.decrypt(destination.secret_ciphertext) == "write-only-webhook-placeholder"
    assert rule.categories == ["security"]
    assert event.metadata_["jsonb"] is True


def test_callback_receipt_uniqueness(postgres_session) -> None:
    postgres_session.add(
        FeishuCallbackReceipt(
            event_id="evt-postgres",
            callback_type="im.chat.member.bot.added_v1",
            payload_hash="hash-1",
        )
    )
    postgres_session.flush()
    postgres_session.add(
        FeishuCallbackReceipt(
            event_id="evt-postgres",
            callback_type="im.chat.member.bot.added_v1",
            payload_hash="hash-2",
        )
    )
    with pytest.raises(IntegrityError):
        postgres_session.flush()
