"""Add durable server-side fiscalization jobs.

Revision ID: 0051_durable_fiscalization_jobs
Revises: 0050_rental_asset_components
Create Date: 2026-10-19
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0051_durable_fiscalization_jobs"
down_revision: str | None = "0050_rental_asset_components"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Persist fiscal work independently of browser and Redis lifecycle."""
    status = postgresql.ENUM(
        "queued",
        "processing",
        "completed",
        "failed",
        name="fiscalization_job_status",
        create_type=False,
    )
    status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "fiscalization_jobs",
        sa.Column("sale_id", sa.Uuid(), nullable=False),
        sa.Column("fiscalization_id", sa.Uuid(), nullable=False),
        sa.Column("status", status, server_default="queued", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("aqsi_operation_id", sa.String(length=255), nullable=True),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
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
        sa.CheckConstraint("attempt_count >= 0", name="ck_fiscalization_jobs_attempt_nonnegative"),
        sa.ForeignKeyConstraint(["sale_id"], ["sales.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["fiscalization_id"], ["fiscalizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["deleted_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sale_id", name="uq_fiscalization_jobs_sale_id"),
        sa.UniqueConstraint("fiscalization_id", name="uq_fiscalization_jobs_fiscalization_id"),
    )
    for column in ("sale_id", "fiscalization_id", "status", "aqsi_operation_id"):
        op.create_index(f"ix_fiscalization_jobs_{column}", "fiscalization_jobs", [column])

    # Existing pending/unknown operations must survive the deployment boundary.
    # Failed rows remain visible for an explicit operator retry; unfinished rows
    # are immediately eligible for the recovery sweep.
    bind = op.get_bind()
    existing = bind.execute(
        sa.text(
            """
            SELECT f.id, f.sale_id, f.status, f.attempt_count, f.external_id,
                   f.created_by_id, f.updated_by_id
            FROM fiscalizations AS f
            JOIN sales AS s ON s.id = f.sale_id
            WHERE f.status IN ('pending', 'unknown', 'failed')
              AND s.status IN ('fiscalization_pending', 'fiscalization_failed')
            """
        )
    ).mappings()
    rows = [
        {
            "id": uuid4(),
            "sale_id": row["sale_id"],
            "fiscalization_id": row["id"],
            "status": "failed" if row["status"] == "failed" else "queued",
            "attempt_count": row["attempt_count"],
            "aqsi_operation_id": row["external_id"],
            "created_by_id": row["created_by_id"],
            "updated_by_id": row["updated_by_id"],
        }
        for row in existing
    ]
    if rows:
        bind.execute(
            sa.text(
                """
                INSERT INTO fiscalization_jobs (
                    id, sale_id, fiscalization_id, status, attempt_count,
                    aqsi_operation_id, created_by_id, updated_by_id
                ) VALUES (
                    :id, :sale_id, :fiscalization_id, :status, :attempt_count,
                    :aqsi_operation_id, :created_by_id, :updated_by_id
                )
                """
            ),
            rows,
        )


def downgrade() -> None:
    """Remove durable fiscal work records."""
    op.drop_table("fiscalization_jobs")
    sa.Enum(name="fiscalization_job_status").drop(op.get_bind(), checkfirst=True)
