"""notifications feishu admin

Revision ID: 0002_notifications_feishu_admin
Revises: 0001_initial
Create Date: 2026-06-19 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_notifications_feishu_admin"
down_revision = "0001_initial"
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


def _uuid_type() -> sa.types.TypeEngine:
    return sa.Uuid() if _is_postgresql() else sa.String(36)


def upgrade() -> None:
    op.create_table(
        "notification_destinations",
        sa.Column("id", _uuid_type(), primary_key=True),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("chat_id", sa.Text()),
        sa.Column("chat_name", sa.Text()),
        sa.Column("secret_ciphertext", sa.Text()),
        sa.Column("secret_fingerprint", sa.Text()),
        sa.Column("config", _json_type(), nullable=False, server_default=_json_default()),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.Column("last_tested_at", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("last_failure_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.Text()),
        sa.Column("last_error_message", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "provider", "chat_id", name="uq_notification_destinations_provider_chat"
        ),
    )
    op.create_index(
        "ix_notification_destinations_key", "notification_destinations", ["key"], unique=True
    )
    op.create_index(
        "ix_notification_destinations_provider", "notification_destinations", ["provider"]
    )
    op.create_index("ix_notification_destinations_status", "notification_destinations", ["status"])
    op.create_index(
        "ix_notification_destinations_chat_id", "notification_destinations", ["chat_id"]
    )

    op.create_table(
        "notification_rules",
        sa.Column("id", _uuid_type(), primary_key=True),
        sa.Column(
            "destination_id",
            _uuid_type(),
            sa.ForeignKey("notification_destinations.id"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("minimum_severity", sa.Text(), nullable=False, server_default="normal"),
        sa.Column(
            "categories", _text_array_type(), nullable=False, server_default=_text_array_default()
        ),
        sa.Column(
            "sources", _text_array_type(), nullable=False, server_default=_text_array_default()
        ),
        sa.Column(
            "symbols", _text_array_type(), nullable=False, server_default=_text_array_default()
        ),
        sa.Column(
            "chains", _text_array_type(), nullable=False, server_default=_text_array_default()
        ),
        sa.Column("delivery_mode", sa.Text(), nullable=False, server_default="immediate"),
        sa.Column("digest_interval_minutes", sa.Integer()),
        sa.Column("quiet_hours_start", sa.Text()),
        sa.Column("quiet_hours_end", sa.Text()),
        sa.Column("timezone", sa.Text(), nullable=False, server_default="UTC"),
        sa.Column("maximum_messages_per_hour", sa.Integer(), nullable=False, server_default="30"),
        sa.Column(
            "critical_bypass_quiet_hours", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_notification_rules_destination_id", "notification_rules", ["destination_id"]
    )

    op.create_table(
        "feishu_callback_receipts",
        sa.Column("id", _uuid_type(), primary_key=True),
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("callback_type", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.Text(), nullable=False),
        sa.Column(
            "received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.Text(), nullable=False, server_default="received"),
        sa.Column("sanitized_error", sa.Text()),
    )
    op.create_index(
        "ix_feishu_callback_receipts_event_id",
        "feishu_callback_receipts",
        ["event_id"],
        unique=True,
    )
    op.create_index(
        "ix_feishu_callback_receipts_callback_type", "feishu_callback_receipts", ["callback_type"]
    )
    op.create_index("ix_feishu_callback_receipts_status", "feishu_callback_receipts", ["status"])

    op.create_table(
        "admin_audit_logs",
        sa.Column("id", _uuid_type(), primary_key=True),
        sa.Column("admin_subject", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.Text(), nullable=False),
        sa.Column("resource_id", sa.Text()),
        sa.Column("metadata", _json_type(), nullable=False, server_default=_json_default()),
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column("ip_hash", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_admin_audit_logs_admin_subject", "admin_audit_logs", ["admin_subject"])
    op.create_index("ix_admin_audit_logs_action", "admin_audit_logs", ["action"])
    op.create_index("ix_admin_audit_logs_resource_type", "admin_audit_logs", ["resource_type"])
    op.create_index("ix_admin_audit_logs_request_id", "admin_audit_logs", ["request_id"])

    with op.batch_alter_table("deliveries") as batch:
        batch.add_column(
            sa.Column(
                "destination_id",
                _uuid_type(),
                sa.ForeignKey(
                    "notification_destinations.id",
                    name="fk_deliveries_destination_id",
                ),
            )
        )
        batch.add_column(
            sa.Column("delivery_variant", sa.Text(), nullable=False, server_default="immediate")
        )
        batch.add_column(sa.Column("provider_message_id", sa.Text()))
        batch.add_column(sa.Column("payload_hash", sa.Text()))
        batch.add_column(sa.Column("response_status", sa.Integer()))
        batch.add_column(sa.Column("retry_after", sa.Integer()))
        batch.add_column(sa.Column("acknowledged_at", sa.DateTime(timezone=True)))
        batch.create_unique_constraint(
            "uq_deliveries_destination_event_variant",
            ["destination_id", "event_id", "delivery_variant"],
        )
    op.create_index("ix_deliveries_destination_id", "deliveries", ["destination_id"])
    op.create_index("ix_deliveries_payload_hash", "deliveries", ["payload_hash"])


def downgrade() -> None:
    op.drop_index("ix_deliveries_payload_hash", table_name="deliveries")
    op.drop_index("ix_deliveries_destination_id", table_name="deliveries")
    with op.batch_alter_table("deliveries") as batch:
        batch.drop_constraint("uq_deliveries_destination_event_variant", type_="unique")
        batch.drop_column("acknowledged_at")
        batch.drop_column("retry_after")
        batch.drop_column("response_status")
        batch.drop_column("payload_hash")
        batch.drop_column("provider_message_id")
        batch.drop_column("delivery_variant")
        batch.drop_column("destination_id")
    op.drop_table("admin_audit_logs")
    op.drop_table("feishu_callback_receipts")
    op.drop_table("notification_rules")
    op.drop_table("notification_destinations")
