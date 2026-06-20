"""system config

Revision ID: 0003_system_config
Revises: 0002_notifications_feishu_admin
Create Date: 2026-06-21 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_system_config"
down_revision = "0002_notifications_feishu_admin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_config",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("value_text", sa.Text()),
        sa.Column("secret_ciphertext", sa.Text()),
        sa.Column("secret_fingerprint", sa.Text()),
        sa.Column("secret_hint", sa.Text()),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("system_config")
