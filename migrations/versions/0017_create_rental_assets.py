"""Create RentalAsset persistence and Intake rental allocation.

Revision ID: 0017_create_rental_assets
Revises: 0016_create_activity_events
Create Date: 2026-07-24 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017_create_rental_assets"
down_revision: str | None = "0016_create_activity_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Store rental allocation on Intake and persist individually tracked assets."""
    op.add_column(
        "intake_item_drafts",
        sa.Column("rental_quantity", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_check_constraint(
        "ck_intake_item_drafts_rental_quantity_valid",
        "intake_item_drafts",
        "rental_quantity >= 0 AND "
        "(quantity IS NULL OR rental_quantity <= quantity)",
    )
    op.execute("CREATE SEQUENCE rental_asset_number_seq START WITH 1")

    purpose = postgresql.ENUM(
        "rental",
        "sale",
        "retired",
        name="asset_purpose",
        create_type=False,
    )
    condition = postgresql.ENUM(
        "new",
        "good",
        "fair",
        "damaged",
        "unusable",
        name="asset_condition",
        create_type=False,
    )
    availability = postgresql.ENUM(
        "available",
        "rented",
        "maintenance",
        name="rental_availability",
        create_type=False,
    )
    purpose.create(op.get_bind(), checkfirst=True)
    condition.create(op.get_bind(), checkfirst=True)
    availability.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "rental_assets",
        sa.Column("asset_number", sa.String(length=32), nullable=False),
        sa.Column("variant_id", sa.Uuid(), nullable=False),
        sa.Column("intake_item_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", purpose, nullable=False),
        sa.Column("condition", condition, nullable=False),
        sa.Column("availability", availability, nullable=False),
        sa.Column("retirement_reason", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["variant_id"], ["catalog_variants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["intake_item_id"],
            ["intake_item_drafts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["deleted_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asset_number"),
    )
    op.create_index(op.f("ix_rental_assets_asset_number"), "rental_assets", ["asset_number"])
    op.create_index(op.f("ix_rental_assets_variant_id"), "rental_assets", ["variant_id"])
    op.create_index(op.f("ix_rental_assets_intake_item_id"), "rental_assets", ["intake_item_id"])


def downgrade() -> None:
    """Remove RentalAsset persistence and Intake rental allocation."""
    op.drop_index(op.f("ix_rental_assets_intake_item_id"), table_name="rental_assets")
    op.drop_index(op.f("ix_rental_assets_variant_id"), table_name="rental_assets")
    op.drop_index(op.f("ix_rental_assets_asset_number"), table_name="rental_assets")
    op.drop_table("rental_assets")
    postgresql.ENUM(name="rental_availability").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="asset_condition").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="asset_purpose").drop(op.get_bind(), checkfirst=True)
    op.execute("DROP SEQUENCE rental_asset_number_seq")
    op.drop_constraint(
        "ck_intake_item_drafts_rental_quantity_valid",
        "intake_item_drafts",
        type_="check",
    )
    op.drop_column("intake_item_drafts", "rental_quantity")
