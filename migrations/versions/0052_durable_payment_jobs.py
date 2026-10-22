"""Add durable server-side acquiring jobs.

Revision ID: 0052_durable_payment_jobs
Revises: 0051_durable_fiscalization_jobs
Create Date: 2026-10-20
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0052_durable_payment_jobs"
down_revision: str | None = "0051_durable_fiscalization_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Persist acquiring continuation independently of browser and Redis."""
    status = postgresql.ENUM(
        "queued",
        "processing",
        "completed",
        "failed",
        name="payment_job_status",
        create_type=False,
    )
    status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "payment_jobs",
        sa.Column("sale_id", sa.Uuid(), nullable=False),
        sa.Column("payment_attempt_id", sa.Uuid(), nullable=False),
        sa.Column("status", status, server_default="queued", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("attempt_count >= 0", name="ck_payment_jobs_attempt_nonnegative"),
        sa.ForeignKeyConstraint(["sale_id"], ["sales.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["payment_attempt_id"], ["payment_attempts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["deleted_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("payment_attempt_id", name="uq_payment_jobs_attempt_id"),
    )
    for column in ("sale_id", "payment_attempt_id", "status"):
        op.create_index(f"ix_payment_jobs_{column}", "payment_jobs", [column])

    bind = op.get_bind()
    pending = bind.execute(
        sa.text(
            """
            SELECT p.id, p.sale_id, p.status, p.external_id,
                   p.created_by_id, p.updated_by_id
            FROM payment_attempts AS p
            JOIN sales AS s ON s.id = p.sale_id
            WHERE p.payment_method = 'card'
              AND p.status IN ('pending', 'unknown')
              AND s.status = 'payment_pending'
            """
        )
    ).mappings()
    rows = [
        {
            "id": uuid4(),
            "sale_id": row["sale_id"],
            "payment_attempt_id": row["id"],
            "status": "queued" if row["external_id"] else "failed",
            "created_by_id": row["created_by_id"],
            "updated_by_id": row["updated_by_id"],
            "last_error": (
                None
                if row["external_id"]
                else "Existing payment has no AQSI operation ID; retry is blocked."
            ),
        }
        for row in pending
    ]
    if rows:
        bind.execute(
            sa.text(
                """
                INSERT INTO payment_jobs (
                    id, sale_id, payment_attempt_id, status,
                    created_by_id, updated_by_id, last_error
                ) VALUES (
                    :id, :sale_id, :payment_attempt_id, :status,
                    :created_by_id, :updated_by_id, :last_error
                )
                """
            ),
            rows,
        )


def downgrade() -> None:
    """Remove durable acquiring work records."""
    op.drop_table("payment_jobs")
    postgresql.ENUM(name="payment_job_status").drop(op.get_bind(), checkfirst=True)
