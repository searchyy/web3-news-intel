"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-18 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def _id_type() -> sa.types.TypeEngine:
    return sa.BigInteger() if _is_postgresql() else sa.Integer()


def _json_type() -> sa.types.TypeEngine:
    return postgresql.JSONB() if _is_postgresql() else sa.JSON()


def _json_default() -> sa.ClauseElement:
    return sa.text("'{}'::jsonb") if _is_postgresql() else sa.text("'{}'")


def _text_array_type() -> sa.types.TypeEngine:
    return postgresql.ARRAY(sa.Text()) if _is_postgresql() else sa.JSON()


def _text_array_default() -> sa.ClauseElement:
    return sa.text("ARRAY[]::text[]") if _is_postgresql() else sa.text("'[]'")


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("id", _id_type(), primary_key=True),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("adapter", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("language", sa.Text()),
        sa.Column("trust_score", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("poll_seconds", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("timeout_seconds", sa.Float(), nullable=False, server_default="15"),
        sa.Column(
            "max_response_bytes",
            sa.Integer(),
            nullable=False,
            server_default=str(2 * 1024 * 1024),
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "allow_private_networks", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("allow_localhost", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("config", _json_type(), nullable=False, server_default=_json_default()),
        sa.Column("access_denied_at", sa.DateTime(timezone=True)),
        sa.Column("access_denied_reason", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_sources_key", "sources", ["key"], unique=True)
    op.create_index("ix_sources_source_type", "sources", ["source_type"])
    op.create_index("ix_sources_category", "sources", ["category"])

    op.create_table(
        "fetch_runs",
        sa.Column("id", _id_type(), primary_key=True),
        sa.Column("source_id", _id_type(), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("http_status", sa.Integer()),
        sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.Text()),
        sa.Column("error_message", sa.Text()),
        sa.Column("trace_id", sa.Text(), nullable=False),
    )
    op.create_index("ix_fetch_runs_source_id", "fetch_runs", ["source_id"])
    op.create_index("ix_fetch_runs_status", "fetch_runs", ["status"])

    op.create_table(
        "raw_documents",
        sa.Column("id", _id_type(), primary_key=True),
        sa.Column("source_id", _id_type(), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("fetch_run_id", _id_type(), sa.ForeignKey("fetch_runs.id")),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text()),
        sa.Column("content_type", sa.Text()),
        sa.Column("status_code", sa.Integer()),
        sa.Column("body_hash", sa.Text(), nullable=False),
        sa.Column("body", sa.Text()),
        sa.Column("metadata", _json_type(), nullable=False, server_default=_json_default()),
        sa.Column(
            "fetched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("source_id", "body_hash", name="uq_raw_documents_source_hash"),
    )
    op.create_index("ix_raw_documents_source_id", "raw_documents", ["source_id"])
    op.create_index("ix_raw_documents_fetch_run_id", "raw_documents", ["fetch_run_id"])

    op.create_table(
        "events",
        sa.Column("id", _id_type(), primary_key=True),
        sa.Column("event_key", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text()),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False, server_default="normal"),
        sa.Column("language", sa.Text()),
        sa.Column("primary_url", sa.Text()),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("trust_score", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("confirmation_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "symbols",
            _text_array_type(),
            nullable=False,
            server_default=_text_array_default(),
        ),
        sa.Column(
            "chains",
            _text_array_type(),
            nullable=False,
            server_default=_text_array_default(),
        ),
        sa.Column(
            "entities",
            _text_array_type(),
            nullable=False,
            server_default=_text_array_default(),
        ),
        sa.Column("metadata", _json_type(), nullable=False, server_default=_json_default()),
    )
    op.create_index("ix_events_event_key", "events", ["event_key"], unique=True)
    op.create_index("ix_events_category", "events", ["category"])
    op.create_index("ix_events_status", "events", ["status"])
    op.create_index("ix_events_severity", "events", ["severity"])
    op.create_index("ix_events_published_at", "events", ["published_at"])

    op.create_table(
        "event_sources",
        sa.Column("id", _id_type(), primary_key=True),
        sa.Column("event_id", _id_type(), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("source_id", _id_type(), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("raw_document_id", _id_type(), sa.ForeignKey("raw_documents.id")),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text()),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("source_score", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "event_id", "source_id", "url", name="uq_event_sources_event_source_url"
        ),
    )
    op.create_index("ix_event_sources_event_id", "event_sources", ["event_id"])
    op.create_index("ix_event_sources_source_id", "event_sources", ["source_id"])
    op.create_index("ix_event_sources_raw_document_id", "event_sources", ["raw_document_id"])

    op.create_table(
        "deliveries",
        sa.Column("id", _id_type(), primary_key=True),
        sa.Column("event_id", _id_type(), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text()),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_deliveries_event_id", "deliveries", ["event_id"])
    op.create_index("ix_deliveries_channel", "deliveries", ["channel"])
    op.create_index("ix_deliveries_status", "deliveries", ["status"])
    op.create_index("ix_deliveries_idempotency_key", "deliveries", ["idempotency_key"], unique=True)


def downgrade() -> None:
    op.drop_table("deliveries")
    op.drop_table("event_sources")
    op.drop_table("events")
    op.drop_table("raw_documents")
    op.drop_table("fetch_runs")
    op.drop_table("sources")
