"""ai job runtime

Revision ID: 0008_ai_job_runtime
Revises: 0007_fetch_run_queue_obs
Create Date: 2026-06-23 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_ai_job_runtime"
down_revision = "0007_fetch_run_queue_obs"
branch_labels = None
depends_on = None


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def _json_type() -> sa.types.TypeEngine:
    return postgresql.JSONB() if _is_postgresql() else sa.JSON()


def _json_list_default() -> sa.ClauseElement:
    return sa.text("'[]'::jsonb") if _is_postgresql() else sa.text("'[]'")


def upgrade() -> None:
    with op.batch_alter_table("event_ai_insights") as batch:
        batch.add_column(
            sa.Column("input_quality", sa.Text(), nullable=False, server_default="title_only")
        )
    with op.batch_alter_table("ai_runs") as batch:
        batch.add_column(sa.Column("error_message_sanitized", sa.Text()))
        batch.add_column(
            sa.Column(
                "event_ids",
                _json_type(),
                nullable=False,
                server_default=_json_list_default(),
            )
        )
        batch.add_column(sa.Column("queued_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("started_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("queue_wait_ms", sa.Integer()))
        batch.add_column(sa.Column("provider_latency_ms", sa.Integer()))
        batch.add_column(sa.Column("total_latency_ms", sa.Integer()))
        batch.add_column(sa.Column("task_id", sa.Text()))
        batch.add_column(sa.Column("worker_name", sa.Text()))
    op.execute("UPDATE ai_runs SET queued_at = created_at WHERE queued_at IS NULL")
    op.execute(
        """
        UPDATE ai_runs
        SET started_at = COALESCE(started_at, created_at)
        WHERE status IN ('running', 'success', 'failed', 'budget_rejected')
        """
    )
    op.execute("UPDATE ai_runs SET status = 'started' WHERE status = 'running'")
    op.execute("UPDATE ai_runs SET status = 'succeeded' WHERE status = 'success'")
    op.execute("UPDATE ai_runs SET status = 'failed' WHERE status = 'budget_rejected'")
    op.execute(
        """
        UPDATE ai_runs
        SET total_latency_ms =
            CAST(EXTRACT(EPOCH FROM (finished_at - started_at)) * 1000 AS INTEGER)
        WHERE finished_at IS NOT NULL
          AND started_at IS NOT NULL
          AND total_latency_ms IS NULL
        """
        if _is_postgresql()
        else """
        UPDATE ai_runs
        SET total_latency_ms =
            CAST((julianday(finished_at) - julianday(started_at)) * 86400000 AS INTEGER)
        WHERE finished_at IS NOT NULL
          AND started_at IS NOT NULL
          AND total_latency_ms IS NULL
        """
    )
    op.create_index("ix_ai_runs_task_id", "ai_runs", ["task_id"])


def downgrade() -> None:
    with op.batch_alter_table("event_ai_insights") as batch:
        batch.drop_column("input_quality")
    op.drop_index("ix_ai_runs_task_id", table_name="ai_runs")
    op.execute("UPDATE ai_runs SET status = 'running' WHERE status = 'started'")
    op.execute("UPDATE ai_runs SET status = 'success' WHERE status = 'succeeded'")
    with op.batch_alter_table("ai_runs") as batch:
        batch.drop_column("worker_name")
        batch.drop_column("task_id")
        batch.drop_column("total_latency_ms")
        batch.drop_column("provider_latency_ms")
        batch.drop_column("queue_wait_ms")
        batch.drop_column("started_at")
        batch.drop_column("queued_at")
        batch.drop_column("event_ids")
        batch.drop_column("error_message_sanitized")
