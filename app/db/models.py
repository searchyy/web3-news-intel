from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

TextArray = postgresql.ARRAY(Text()).with_variant(JSON(), "sqlite")
JsonDocument = postgresql.JSONB().with_variant(JSON(), "sqlite")
BigIntPk = BigInteger().with_variant(Integer(), "sqlite")


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(Text, unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    adapter: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    language: Mapped[str | None] = mapped_column(Text)
    trust_score: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    poll_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    timeout_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=15.0)
    max_response_bytes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=2 * 1024 * 1024
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    allow_private_networks: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    allow_localhost: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    config: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False, default=dict)
    access_denied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    access_denied_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    fetch_runs: Mapped[list[FetchRun]] = relationship(back_populates="source")
    raw_documents: Mapped[list[RawDocument]] = relationship(back_populates="source")
    event_sources: Mapped[list[EventSource]] = relationship(back_populates="source")


class FetchRun(Base):
    __tablename__ = "fetch_runs"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    http_status: Mapped[int | None] = mapped_column(Integer)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    trace_id: Mapped[str] = mapped_column(Text, nullable=False)

    source: Mapped[Source] = relationship(back_populates="fetch_runs")
    raw_documents: Mapped[list[RawDocument]] = relationship(back_populates="fetch_run")


class RawDocument(Base):
    __tablename__ = "raw_documents"
    __table_args__ = (
        UniqueConstraint("source_id", "body_hash", name="uq_raw_documents_source_hash"),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    fetch_run_id: Mapped[int | None] = mapped_column(ForeignKey("fetch_runs.id"), index=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[str | None] = mapped_column(Text)
    content_type: Mapped[str | None] = mapped_column(Text)
    status_code: Mapped[int | None] = mapped_column(Integer)
    body_hash: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JsonDocument, nullable=False, default=dict
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    source: Mapped[Source] = relationship(back_populates="raw_documents")
    fetch_run: Mapped[FetchRun | None] = relationship(back_populates="raw_documents")
    event_sources: Mapped[list[EventSource]] = relationship(back_populates="raw_document")


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    event_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    severity: Mapped[str] = mapped_column(Text, nullable=False, default="normal", index=True)
    language: Mapped[str | None] = mapped_column(Text)
    primary_url: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    trust_score: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    confirmation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    symbols: Mapped[list[str]] = mapped_column(TextArray, nullable=False, default=list)
    chains: Mapped[list[str]] = mapped_column(TextArray, nullable=False, default=list)
    entities: Mapped[list[str]] = mapped_column(TextArray, nullable=False, default=list)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JsonDocument, nullable=False, default=dict
    )

    sources: Mapped[list[EventSource]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )
    deliveries: Mapped[list[Delivery]] = relationship(back_populates="event")


class NotificationDestination(Base):
    __tablename__ = "notification_destinations"
    __table_args__ = (
        UniqueConstraint("provider", "chat_id", name="uq_notification_destinations_provider_chat"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(Text, unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending", index=True)
    chat_id: Mapped[str | None] = mapped_column(Text, index=True)
    chat_name: Mapped[str | None] = mapped_column(Text)
    secret_ciphertext: Mapped[str | None] = mapped_column(Text)
    secret_fingerprint: Mapped[str | None] = mapped_column(Text)
    config: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False, default=dict)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(Text)
    last_error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    rules: Mapped[list[NotificationRule]] = relationship(
        back_populates="destination", cascade="all, delete-orphan"
    )
    deliveries: Mapped[list[Delivery]] = relationship(back_populates="destination")


class NotificationRule(Base):
    __tablename__ = "notification_rules"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    destination_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("notification_destinations.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    minimum_severity: Mapped[str] = mapped_column(Text, nullable=False, default="normal")
    categories: Mapped[list[str]] = mapped_column(TextArray, nullable=False, default=list)
    sources: Mapped[list[str]] = mapped_column(TextArray, nullable=False, default=list)
    symbols: Mapped[list[str]] = mapped_column(TextArray, nullable=False, default=list)
    chains: Mapped[list[str]] = mapped_column(TextArray, nullable=False, default=list)
    delivery_mode: Mapped[str] = mapped_column(Text, nullable=False, default="immediate")
    digest_interval_minutes: Mapped[int | None] = mapped_column(Integer)
    quiet_hours_start: Mapped[str | None] = mapped_column(Text)
    quiet_hours_end: Mapped[str | None] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text, nullable=False, default="UTC")
    maximum_messages_per_hour: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    critical_bypass_quiet_hours: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    destination: Mapped[NotificationDestination] = relationship(back_populates="rules")


class FeishuCallbackReceipt(Base):
    __tablename__ = "feishu_callback_receipts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    event_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False, index=True)
    callback_type: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    payload_hash: Mapped[str] = mapped_column(Text, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False, default="received", index=True)
    sanitized_error: Mapped[str | None] = mapped_column(Text)


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    admin_subject: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    action: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    resource_id: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JsonDocument, nullable=False, default=dict
    )
    request_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    ip_hash: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SystemConfig(Base):
    __tablename__ = "system_config"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value_text: Mapped[str | None] = mapped_column(Text)
    secret_ciphertext: Mapped[str | None] = mapped_column(Text)
    secret_fingerprint: Mapped[str | None] = mapped_column(Text)
    secret_hint: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class EventSource(Base):
    __tablename__ = "event_sources"
    __table_args__ = (
        UniqueConstraint("event_id", "source_id", "url", name="uq_event_sources_event_source_url"),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    raw_document_id: Mapped[int | None] = mapped_column(ForeignKey("raw_documents.id"), index=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_score: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    event: Mapped[Event] = relationship(back_populates="sources")
    source: Mapped[Source] = relationship(back_populates="event_sources")
    raw_document: Mapped[RawDocument | None] = relationship(back_populates="event_sources")


class Delivery(Base):
    __tablename__ = "deliveries"
    __table_args__ = (
        UniqueConstraint(
            "destination_id",
            "event_id",
            "delivery_variant",
            name="uq_deliveries_destination_event_variant",
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    destination_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("notification_destinations.id"), index=True
    )
    channel: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    target: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True, index=True)
    delivery_variant: Mapped[str] = mapped_column(Text, nullable=False, default="immediate")
    provider_message_id: Mapped[str | None] = mapped_column(Text)
    payload_hash: Mapped[str | None] = mapped_column(Text, index=True)
    response_status: Mapped[int | None] = mapped_column(Integer)
    retry_after: Mapped[int | None] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    event: Mapped[Event] = relationship(back_populates="deliveries")
    destination: Mapped[NotificationDestination | None] = relationship(back_populates="deliveries")
