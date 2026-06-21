"""feishu report schedules

Revision ID: 0005_feishu_report_schedules
Revises: 0004_event_search, 0004_ai_backend
Create Date: 2026-06-21 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_feishu_report_schedules"
down_revision = ("0004_event_search", "0004_ai_backend")
branch_labels = None
depends_on = None


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def _id_type() -> sa.types.TypeEngine:
    return sa.BigInteger() if _is_postgresql() else sa.Integer()


def _text_array_type() -> sa.types.TypeEngine:
    return postgresql.ARRAY(sa.Text()) if _is_postgresql() else sa.JSON()


def _text_array_default() -> sa.ClauseElement:
    return sa.text("ARRAY[]::text[]") if _is_postgresql() else sa.text("'[]'")


def _uuid_type() -> sa.types.TypeEngine:
    return sa.Uuid() if _is_postgresql() else sa.String(36)


def upgrade() -> None:
    op.create_table(
        "report_schedules",
        sa.Column("id", _id_type(), primary_key=True),
        sa.Column(
            "destination_id",
            _uuid_type(),
            sa.ForeignKey("notification_destinations.id"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("report_type", sa.Text(), nullable=False),
        sa.Column("timezone", sa.Text(), nullable=False, server_default="UTC"),
        sa.Column("interval_minutes", sa.Integer()),
        sa.Column("hour", sa.Integer()),
        sa.Column("minute", sa.Integer()),
        sa.Column("saved_search_id", _id_type(), sa.ForeignKey("saved_searches.id")),
        sa.Column(
            "source_groups",
            _text_array_type(),
            nullable=False,
            server_default=_text_array_default(),
        ),
        sa.Column(
            "categories",
            _text_array_type(),
            nullable=False,
            server_default=_text_array_default(),
        ),
        sa.Column(
            "severities",
            _text_array_type(),
            nullable=False,
            server_default=_text_array_default(),
        ),
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
        sa.Column("minimum_trust_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("include_ai_summary", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("maximum_events", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.Column("last_window_start", sa.DateTime(timezone=True)),
        sa.Column("last_window_end", sa.DateTime(timezone=True)),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column("next_run_at", sa.DateTime(timezone=True)),
        sa.Column("last_result", sa.Text()),
        sa.Column("last_error_sanitized", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_report_schedules_destination_id", "report_schedules", ["destination_id"])
    op.create_index("ix_report_schedules_enabled", "report_schedules", ["enabled"])
    op.create_index("ix_report_schedules_report_type", "report_schedules", ["report_type"])
    op.create_index("ix_report_schedules_next_run_at", "report_schedules", ["next_run_at"])
    op.create_index("ix_report_schedules_saved_search_id", "report_schedules", ["saved_search_id"])


def downgrade() -> None:
    op.drop_index("ix_report_schedules_saved_search_id", table_name="report_schedules")
    op.drop_index("ix_report_schedules_next_run_at", table_name="report_schedules")
    op.drop_index("ix_report_schedules_report_type", table_name="report_schedules")
    op.drop_index("ix_report_schedules_enabled", table_name="report_schedules")
    op.drop_index("ix_report_schedules_destination_id", table_name="report_schedules")
    op.drop_table("report_schedules")
