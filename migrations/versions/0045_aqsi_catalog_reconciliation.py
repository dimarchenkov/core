"""Persist audited AQSI catalog reconciliation results.

Revision ID: 0045_aqsi_reconciliation
Revises: 0044_rental_return_overdue
Create Date: 2026-10-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0045_aqsi_reconciliation"
down_revision: str | None = "0044_rental_return_overdue"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create append-only reconciliation history with bounded diagnostics."""
    op.create_table(
        "aqsi_catalog_reconciliation_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("updated_by_id", sa.Uuid(), nullable=True),
        sa.Column("integration_id", sa.Uuid(), nullable=False),
        sa.Column("trigger", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expected_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("skipped_not_ready_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("remote_total_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("remote_managed_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("unmanaged_remote_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("missing_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("mismatched_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "deletion_candidate_count", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("queued_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("queue_failure_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "deletion_guard_triggered",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column(
            "destructive_actions_blocked",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column(
            "missing_external_ids", sa.JSON(), server_default="[]", nullable=False
        ),
        sa.Column(
            "mismatched_external_ids", sa.JSON(), server_default="[]", nullable=False
        ),
        sa.Column(
            "deletion_candidate_external_ids",
            sa.JSON(),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["integration_id"], ["integrations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["deleted_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_aqsi_catalog_reconciliation_runs_integration_id",
        "aqsi_catalog_reconciliation_runs",
        ["integration_id"],
    )
    op.create_index(
        "ix_aqsi_catalog_reconciliation_runs_status",
        "aqsi_catalog_reconciliation_runs",
        ["status"],
    )
    op.create_index(
        "ix_aqsi_catalog_reconciliation_integration_started",
        "aqsi_catalog_reconciliation_runs",
        ["integration_id", "started_at"],
    )


def downgrade() -> None:
    """Remove AQSI reconciliation history."""
    op.drop_index(
        "ix_aqsi_catalog_reconciliation_integration_started",
        table_name="aqsi_catalog_reconciliation_runs",
    )
    op.drop_index(
        "ix_aqsi_catalog_reconciliation_runs_status",
        table_name="aqsi_catalog_reconciliation_runs",
    )
    op.drop_index(
        "ix_aqsi_catalog_reconciliation_runs_integration_id",
        table_name="aqsi_catalog_reconciliation_runs",
    )
    op.drop_table("aqsi_catalog_reconciliation_runs")
