"""Create identity foundation tables.

Revision ID: 0006_create_identity_foundation
Revises: 0005_create_media_foundation
Create Date: 2026-07-11 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_create_identity_foundation"
down_revision: str | None = "0005_create_media_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create users and append-only privilege audit event tables."""
    action = postgresql.ENUM(
        "superuser_enabled",
        "superuser_disabled",
        name="privilege_audit_action",
        create_type=False,
    )
    action.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "users",
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=1024), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("is_admin", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("is_superuser", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("updated_by_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"])
    op.create_table(
        "privilege_audit_events",
        sa.Column("target_user_id", sa.Uuid(), nullable=False),
        sa.Column("action", action, nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("actor_description", sa.String(length=255), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["target_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_privilege_audit_events_target_user_id"),
        "privilege_audit_events",
        ["target_user_id"],
    )


def downgrade() -> None:
    """Drop identity foundation tables and the privilege action enum."""
    op.drop_index(
        op.f("ix_privilege_audit_events_target_user_id"),
        table_name="privilege_audit_events",
    )
    op.drop_table("privilege_audit_events")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")
    postgresql.ENUM(name="privilege_audit_action").drop(op.get_bind(), checkfirst=True)
