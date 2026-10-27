"""Add immutable financial events and durable dispatch markers.

Revision ID: 0053_financial_operation_journal
Revises: 0052_durable_payment_jobs
Create Date: 2026-10-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0053_financial_operation_journal"
down_revision: str | None = "0052_durable_payment_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add append-only evidence without rewriting existing financial facts."""
    entity_type = postgresql.ENUM(
        "payment_attempt",
        "fiscalization",
        "payment_job",
        "fiscalization_job",
        "reconciliation",
        name="financial_entity_type",
        create_type=False,
    )
    event_type = postgresql.ENUM(
        "payment_created",
        "payment_command_prepared",
        "payment_dispatch_started",
        "payment_operation_id_received",
        "payment_approved",
        "payment_declined",
        "payment_canceled",
        "payment_outcome_unknown",
        "fiscalization_created",
        "fiscal_command_prepared",
        "fiscal_dispatch_started",
        "fiscal_operation_id_received",
        "fiscalization_succeeded",
        "fiscalization_failed",
        "fiscal_outcome_unknown",
        "reconciliation_requested",
        "reconciliation_succeeded",
        "reconciliation_inconclusive",
        "manual_review_required",
        name="financial_event_type",
        create_type=False,
    )
    actor_type = postgresql.ENUM(
        "operator",
        "worker",
        "system",
        name="financial_actor_type",
        create_type=False,
    )
    entity_type.create(op.get_bind(), checkfirst=True)
    event_type.create(op.get_bind(), checkfirst=True)
    actor_type.create(op.get_bind(), checkfirst=True)

    for table_name, prefix in (
        ("payment_jobs", "payment"),
        ("fiscalization_jobs", "fiscal"),
    ):
        op.add_column(table_name, sa.Column("correlation_id", sa.String(255), nullable=True))
        op.add_column(
            table_name,
            sa.Column("dispatch_started_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.add_column(
            table_name,
            sa.Column("reconciliation_requested_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.execute(
            sa.text(
                f"UPDATE {table_name} SET correlation_id = :prefix || ':' || id::text "
                "WHERE correlation_id IS NULL"
            ).bindparams(prefix=prefix)
        )
        op.alter_column(table_name, "correlation_id", nullable=False)
        op.create_unique_constraint(
            f"uq_{table_name}_correlation_id",
            table_name,
            ["correlation_id"],
        )

    op.create_table(
        "financial_operation_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("sale_id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", entity_type, nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", event_type, nullable=False),
        sa.Column("previous_status", sa.String(64), nullable=True),
        sa.Column("new_status", sa.String(64), nullable=True),
        sa.Column(
            "provider",
            postgresql.ENUM(name="integration_provider", create_type=False),
            nullable=True,
        ),
        sa.Column("provider_operation_id", sa.String(255), nullable=True),
        sa.Column("actor_type", actor_type, nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("correlation_id", sa.String(255), nullable=False),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("safe_message", sa.String(1000), nullable=True),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["sale_id"], ["sales.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_financial_operation_events_sale_id", "financial_operation_events", ["sale_id"]
    )
    op.create_index(
        "ix_financial_operation_events_event_type", "financial_operation_events", ["event_type"]
    )
    op.create_index(
        "ix_financial_operation_events_correlation_id",
        "financial_operation_events",
        ["correlation_id"],
    )
    op.create_index(
        "ix_financial_operation_events_occurred_at", "financial_operation_events", ["occurred_at"]
    )
    op.create_index(
        "ix_financial_events_entity", "financial_operation_events", ["entity_type", "entity_id"]
    )
    op.create_index(
        "ix_financial_events_sale_occurred",
        "financial_operation_events",
        ["sale_id", "occurred_at"],
    )


def downgrade() -> None:
    """Remove Phase B evidence structures without touching Sales facts."""
    op.drop_table("financial_operation_events")
    for table_name in ("fiscalization_jobs", "payment_jobs"):
        op.drop_constraint(f"uq_{table_name}_correlation_id", table_name=table_name, type_="unique")
        op.drop_column(table_name, "reconciliation_requested_at")
        op.drop_column(table_name, "dispatch_started_at")
        op.drop_column(table_name, "correlation_id")
    postgresql.ENUM(name="financial_actor_type").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="financial_event_type").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="financial_entity_type").drop(op.get_bind(), checkfirst=True)
