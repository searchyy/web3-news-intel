"""event search indexes and saved searches

Revision ID: 0004_event_search
Revises: 0003_system_config
Create Date: 2026-06-21 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_event_search"
down_revision = "0003_system_config"
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


def upgrade() -> None:
    op.create_table(
        "saved_searches",
        sa.Column("id", _id_type(), primary_key=True),
        sa.Column("owner_subject", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("filters", _json_type(), nullable=False, server_default=_json_default()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("owner_subject", "name", name="uq_saved_searches_owner_name"),
    )
    op.create_index("ix_saved_searches_owner_subject", "saved_searches", ["owner_subject"])
    op.create_index("ix_saved_searches_updated_at", "saved_searches", ["updated_at"])

    op.create_index("ix_events_first_seen_at", "events", ["first_seen_at"])
    op.create_index("ix_events_last_seen_at", "events", ["last_seen_at"])
    op.create_index("ix_events_trust_score", "events", ["trust_score"])
    op.create_index(
        "ix_events_status_severity_first_seen",
        "events",
        ["status", "severity", "first_seen_at"],
    )
    op.create_index("ix_events_category_first_seen", "events", ["category", "first_seen_at"])
    op.create_index(
        "ix_event_sources_source_event",
        "event_sources",
        ["source_id", "event_id"],
    )

    if _is_postgresql():
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        op.execute(
            "CREATE INDEX ix_events_title_trgm "
            "ON events USING gin (lower(coalesce(title, '')) gin_trgm_ops)"
        )
        op.execute(
            "CREATE INDEX ix_events_summary_trgm "
            "ON events USING gin (lower(coalesce(summary, '')) gin_trgm_ops)"
        )
        op.execute("CREATE INDEX ix_events_symbols_gin ON events USING gin (symbols)")
        op.execute("CREATE INDEX ix_events_chains_gin ON events USING gin (chains)")
        op.execute("CREATE INDEX ix_events_entities_gin ON events USING gin (entities)")


def downgrade() -> None:
    if _is_postgresql():
        op.execute("DROP INDEX IF EXISTS ix_events_entities_gin")
        op.execute("DROP INDEX IF EXISTS ix_events_chains_gin")
        op.execute("DROP INDEX IF EXISTS ix_events_symbols_gin")
        op.execute("DROP INDEX IF EXISTS ix_events_summary_trgm")
        op.execute("DROP INDEX IF EXISTS ix_events_title_trgm")
    op.drop_index("ix_event_sources_source_event", table_name="event_sources")
    op.drop_index("ix_events_category_first_seen", table_name="events")
    op.drop_index("ix_events_status_severity_first_seen", table_name="events")
    op.drop_index("ix_events_trust_score", table_name="events")
    op.drop_index("ix_events_last_seen_at", table_name="events")
    op.drop_index("ix_events_first_seen_at", table_name="events")
    op.drop_index("ix_saved_searches_updated_at", table_name="saved_searches")
    op.drop_index("ix_saved_searches_owner_subject", table_name="saved_searches")
    op.drop_table("saved_searches")
