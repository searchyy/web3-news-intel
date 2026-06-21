"""ai backend

Revision ID: 0004_ai_backend
Revises: 0003_system_config
Create Date: 2026-06-21 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_ai_backend"
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


def _json_list_default() -> sa.ClauseElement:
    return sa.text("'[]'::jsonb") if _is_postgresql() else sa.text("'[]'")


def _text_array_type() -> sa.types.TypeEngine:
    return postgresql.ARRAY(sa.Text()) if _is_postgresql() else sa.JSON()


def _text_array_default() -> sa.ClauseElement:
    return sa.text("ARRAY[]::text[]") if _is_postgresql() else sa.text("'[]'")


def upgrade() -> None:
    op.create_table(
        "ai_provider_configs",
        sa.Column("id", _id_type(), primary_key=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("api_base", sa.Text(), nullable=False),
        sa.Column("api_key_ciphertext", sa.Text()),
        sa.Column("api_key_fingerprint", sa.Text()),
        sa.Column("model", sa.Text()),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="90"),
        sa.Column("max_concurrency", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("max_tokens", sa.Integer(), nullable=False, server_default="1200"),
        sa.Column("temperature", sa.Float(), nullable=False, server_default="0.2"),
        sa.Column("thinking_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("daily_token_budget", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("daily_request_budget", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "auto_process_enabled", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("auto_minimum_severity", sa.Text(), nullable=False, server_default="high"),
        sa.Column("config", _json_type(), nullable=False, server_default=_json_default()),
        sa.Column("last_tested_at", sa.DateTime(timezone=True)),
        sa.Column("last_test_status", sa.Text()),
        sa.Column("last_error_sanitized", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_ai_provider_configs_provider",
        "ai_provider_configs",
        ["provider"],
        unique=True,
    )

    op.create_table(
        "ai_prompt_templates",
        sa.Column("id", _id_type(), primary_key=True),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("user_prompt_template", sa.Text(), nullable=False),
        sa.Column("output_schema_version", sa.Text(), nullable=False, server_default="v1"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Text(), nullable=False, server_default="v1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("key", "version", name="uq_ai_prompt_templates_key_version"),
    )
    op.create_index("ix_ai_prompt_templates_key", "ai_prompt_templates", ["key"])

    op.create_table(
        "event_ai_insights",
        sa.Column("id", _id_type(), primary_key=True),
        sa.Column("event_id", _id_type(), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("input_hash", sa.Text(), nullable=False),
        sa.Column("summary_zh", sa.Text()),
        sa.Column("headline_zh", sa.Text()),
        sa.Column("key_facts", _json_type(), nullable=False, server_default=_json_list_default()),
        sa.Column("entities", _json_type(), nullable=False, server_default=_json_list_default()),
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
        sa.Column("event_type", sa.Text()),
        sa.Column("importance_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("risk_level", sa.Text(), nullable=False, server_default="low"),
        sa.Column("sentiment", sa.Text(), nullable=False, server_default="neutral"),
        sa.Column("market_impact", sa.Text()),
        sa.Column("facts", _json_type(), nullable=False, server_default=_json_list_default()),
        sa.Column("inferences", _json_type(), nullable=False, server_default=_json_list_default()),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column(
            "source_event_ids",
            _text_array_type(),
            nullable=False,
            server_default=_text_array_default(),
        ),
        sa.Column(
            "source_urls",
            _text_array_type(),
            nullable=False,
            server_default=_text_array_default(),
        ),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("generated_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("error_sanitized", sa.Text()),
        sa.UniqueConstraint(
            "event_id",
            "provider",
            "model",
            "prompt_version",
            "input_hash",
            name="uq_event_ai_insights_input",
        ),
    )
    op.create_index("ix_event_ai_insights_event_id", "event_ai_insights", ["event_id"])
    op.create_index("ix_event_ai_insights_provider", "event_ai_insights", ["provider"])
    op.create_index("ix_event_ai_insights_input_hash", "event_ai_insights", ["input_hash"])
    op.create_index("ix_event_ai_insights_status", "event_ai_insights", ["status"])

    op.create_table(
        "ai_runs",
        sa.Column("id", _id_type(), primary_key=True),
        sa.Column("job_type", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text()),
        sa.Column("event_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_cost", sa.Float()),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.Text()),
        sa.Column("error_sanitized", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_ai_runs_job_type", "ai_runs", ["job_type"])
    op.create_index("ix_ai_runs_provider", "ai_runs", ["provider"])
    op.create_index("ix_ai_runs_status", "ai_runs", ["status"])
    op.create_index("ix_ai_runs_created_at", "ai_runs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_ai_runs_created_at", table_name="ai_runs")
    op.drop_index("ix_ai_runs_status", table_name="ai_runs")
    op.drop_index("ix_ai_runs_provider", table_name="ai_runs")
    op.drop_index("ix_ai_runs_job_type", table_name="ai_runs")
    op.drop_table("ai_runs")
    op.drop_index("ix_event_ai_insights_status", table_name="event_ai_insights")
    op.drop_index("ix_event_ai_insights_input_hash", table_name="event_ai_insights")
    op.drop_index("ix_event_ai_insights_provider", table_name="event_ai_insights")
    op.drop_index("ix_event_ai_insights_event_id", table_name="event_ai_insights")
    op.drop_table("event_ai_insights")
    op.drop_index("ix_ai_prompt_templates_key", table_name="ai_prompt_templates")
    op.drop_table("ai_prompt_templates")
    op.drop_index("ix_ai_provider_configs_provider", table_name="ai_provider_configs")
    op.drop_table("ai_provider_configs")
