"""Add durable payment and fiscal incident resolutions.

Revision ID: 0055_incident_resolutions
Revises: 0054_emergency_checkout_fallback
Create Date: 2026-10-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0055_incident_resolutions"
down_revision: str | None = "0054_emergency_checkout_fallback"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add current resolution state while preserving all original financial facts."""
    for value in (
        "payment_incident_reviewed",
        "single_charge_operator_confirmed",
        "payment_incident_resolved",
        "fiscal_receipt_missing_reported",
        "fiscal_receipt_verified",
        "fiscal_verification_unknown_recorded",
    ):
        op.execute(f"ALTER TYPE financial_event_type ADD VALUE IF NOT EXISTS '{value}'")

    incident_kind = postgresql.ENUM(
        "payment_duplicate_risk",
        "fiscal_receipt_verification",
        name="incident_kind",
        create_type=False,
    )
    resolution_type = postgresql.ENUM(
        "single_charge_operator_confirmed",
        "fiscal_receipt_verified",
        "fiscal_receipt_missing",
        "fiscal_verification_unknown",
        name="incident_resolution_type",
        create_type=False,
    )
    resolution_status = postgresql.ENUM(
        "resolved",
        "requires_action",
        "unknown",
        name="incident_resolution_status",
        create_type=False,
    )
    incident_kind.create(op.get_bind(), checkfirst=True)
    resolution_type.create(op.get_bind(), checkfirst=True)
    resolution_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "financial_incident_resolutions",
        sa.Column("sale_id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("incident_kind", incident_kind, nullable=False),
        sa.Column("resolution_type", resolution_type, nullable=False),
        sa.Column("resolution_status", resolution_status, nullable=False),
        sa.Column("resolved_by_id", sa.Uuid(), nullable=False),
        sa.Column(
            "resolved_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("evidence_reference", sa.String(255), nullable=True),
        sa.Column("operator_note", sa.String(1000), nullable=True),
        sa.Column("fiscal_fn", sa.String(64), nullable=True),
        sa.Column("fiscal_fd", sa.String(64), nullable=True),
        sa.Column("fiscal_fpd", sa.String(128), nullable=True),
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
        sa.ForeignKeyConstraint(["sale_id"], ["sales.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["resolved_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["deleted_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "incident_kind",
            "incident_id",
            name="uq_financial_incident_resolution_kind_id",
        ),
    )
    op.create_index(
        "ix_financial_incident_resolutions_sale_id",
        "financial_incident_resolutions",
        ["sale_id"],
    )
    op.create_index(
        "ix_financial_incident_resolutions_incident_id",
        "financial_incident_resolutions",
        ["incident_id"],
    )
    op.create_index(
        "ix_financial_incident_resolutions_resolved_by_id",
        "financial_incident_resolutions",
        ["resolved_by_id"],
    )
    op.create_index(
        "ix_financial_incident_resolutions_sale_kind",
        "financial_incident_resolutions",
        ["sale_id", "incident_kind"],
    )


def downgrade() -> None:
    """Drop only resolution state; keep append-only journal enum values intact."""
    op.drop_table("financial_incident_resolutions")
    postgresql.ENUM(name="incident_resolution_status").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="incident_resolution_type").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="incident_kind").drop(op.get_bind(), checkfirst=True)
