"""source catalog metadata

Revision ID: 0006_source_catalog_metadata
Revises: 0005_feishu_report_schedules
Create Date: 2026-06-21 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_source_catalog_metadata"
down_revision = "0005_feishu_report_schedules"
branch_labels = None
depends_on = None


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def _text_array_type() -> sa.types.TypeEngine:
    return postgresql.ARRAY(sa.Text()) if _is_postgresql() else sa.JSON()


def _text_array_default() -> sa.ClauseElement:
    return sa.text("ARRAY[]::text[]") if _is_postgresql() else sa.text("'[]'")


def upgrade() -> None:
    with op.batch_alter_table("sources") as batch:
        batch.add_column(sa.Column("display_name_zh", sa.Text()))
        batch.add_column(
            sa.Column("source_group", sa.Text(), nullable=False, server_default="legacy")
        )
        batch.add_column(
            sa.Column("official", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(
            sa.Column(
                "max_items_per_fetch",
                sa.Integer(),
                nullable=False,
                server_default="50",
            )
        )
        batch.add_column(sa.Column("ranking_provider", sa.Text()))
        batch.add_column(sa.Column("ranking_position", sa.Integer()))
        batch.add_column(sa.Column("ranking_snapshot_at", sa.DateTime(timezone=True)))
        batch.add_column(
            sa.Column("parser_version", sa.Text(), nullable=False, server_default="v1")
        )
        batch.add_column(
            sa.Column(
                "supported_categories",
                _text_array_type(),
                nullable=False,
                server_default=_text_array_default(),
            )
        )
        batch.add_column(
            sa.Column("health_status", sa.Text(), nullable=False, server_default="unknown")
        )
        batch.add_column(
            sa.Column("live_canary_status", sa.Text(), nullable=False, server_default="unknown")
        )
        batch.add_column(sa.Column("last_canary_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("last_canary_error", sa.Text()))
        batch.add_column(sa.Column("last_fetch_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("last_success_at", sa.DateTime(timezone=True)))
        batch.add_column(
            sa.Column(
                "last_parsed_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch.add_column(sa.Column("last_http_status", sa.Integer()))
        batch.add_column(sa.Column("last_error", sa.Text()))
        batch.add_column(sa.Column("access_denied_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("access_denied_reason", sa.Text()))
        batch.add_column(sa.Column("etag", sa.Text()))
        batch.add_column(sa.Column("last_modified", sa.Text()))
        batch.add_column(sa.Column("cursor", sa.Text()))
        batch.add_column(
            sa.Column(
                "consecutive_failures",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch.add_column(sa.Column("circuit_open_until", sa.DateTime(timezone=True)))
    op.create_index("ix_sources_source_group", "sources", ["source_group"])
    op.create_index("ix_sources_official", "sources", ["official"])
    op.create_index("ix_sources_health_status", "sources", ["health_status"])


def downgrade() -> None:
    op.drop_index("ix_sources_health_status", table_name="sources")
    op.drop_index("ix_sources_official", table_name="sources")
    op.drop_index("ix_sources_source_group", table_name="sources")
    with op.batch_alter_table("sources") as batch:
        batch.drop_column("circuit_open_until")
        batch.drop_column("consecutive_failures")
        batch.drop_column("cursor")
        batch.drop_column("last_modified")
        batch.drop_column("etag")
        batch.drop_column("last_error")
        batch.drop_column("access_denied_reason")
        batch.drop_column("access_denied_at")
        batch.drop_column("last_http_status")
        batch.drop_column("last_parsed_count")
        batch.drop_column("last_success_at")
        batch.drop_column("last_fetch_at")
        batch.drop_column("last_canary_error")
        batch.drop_column("last_canary_at")
        batch.drop_column("live_canary_status")
        batch.drop_column("health_status")
        batch.drop_column("supported_categories")
        batch.drop_column("parser_version")
        batch.drop_column("ranking_snapshot_at")
        batch.drop_column("ranking_position")
        batch.drop_column("ranking_provider")
        batch.drop_column("max_items_per_fetch")
        batch.drop_column("official")
        batch.drop_column("source_group")
        batch.drop_column("display_name_zh")
