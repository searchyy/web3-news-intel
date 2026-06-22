"""fetch run queue observability

Revision ID: 0007_fetch_run_queue_obs
Revises: 0006_source_catalog_metadata
Create Date: 2026-06-22 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_fetch_run_queue_obs"
down_revision = "0006_source_catalog_metadata"
branch_labels = None
depends_on = None


ACTIVE_STATUS_SQL = "status IN ('queued', 'running')"


def upgrade() -> None:
    with op.batch_alter_table("fetch_runs") as batch:
        batch.add_column(sa.Column("queued_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("worker_started_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("task_id", sa.Text()))
        batch.add_column(sa.Column("retry_after_until", sa.DateTime(timezone=True)))

    op.execute("UPDATE fetch_runs SET queued_at = started_at WHERE queued_at IS NULL")
    op.execute(
        """
        WITH ranked AS (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY source_id
                    ORDER BY started_at ASC, id ASC
                ) AS rn
            FROM fetch_runs
            WHERE status IN ('queued', 'running')
        )
        UPDATE fetch_runs
        SET
            status = 'skipped',
            finished_at = CURRENT_TIMESTAMP,
            error_code = 'duplicate_active_fetch',
            error_message = 'duplicate_active_fetch'
        WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
        """
    )
    op.create_index(
        "ix_fetch_runs_source_status_started",
        "fetch_runs",
        ["source_id", "status", "started_at"],
    )
    op.create_index("ix_fetch_runs_task_id", "fetch_runs", ["task_id"])
    op.create_index(
        "uq_fetch_runs_active_source",
        "fetch_runs",
        ["source_id"],
        unique=True,
        postgresql_where=sa.text(ACTIVE_STATUS_SQL),
        sqlite_where=sa.text(ACTIVE_STATUS_SQL),
    )


def downgrade() -> None:
    op.drop_index("uq_fetch_runs_active_source", table_name="fetch_runs")
    op.drop_index("ix_fetch_runs_task_id", table_name="fetch_runs")
    op.drop_index("ix_fetch_runs_source_status_started", table_name="fetch_runs")
    with op.batch_alter_table("fetch_runs") as batch:
        batch.drop_column("retry_after_until")
        batch.drop_column("task_id")
        batch.drop_column("worker_started_at")
        batch.drop_column("queued_at")
