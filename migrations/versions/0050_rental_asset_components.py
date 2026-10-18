"""Add RentalAsset component checklists and immutable return verification.

Revision ID: 0050_rental_asset_components
Revises: 0049_variant_sort_order
Create Date: 2026-10-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0050_rental_asset_components"
down_revision: str | None = "0049_variant_sort_order"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Persist optional ordered components and immutable return observations."""
    op.create_table(
        "rental_asset_components",
        sa.Column("rental_asset_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["rental_asset_id"], ["rental_assets.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["deleted_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_rental_asset_components_rental_asset_id",
        "rental_asset_components",
        ["rental_asset_id"],
        unique=False,
    )
    op.create_index(
        "ix_rental_asset_components_is_active",
        "rental_asset_components",
        ["is_active"],
        unique=False,
    )
    op.add_column(
        "rental_order_items",
        sa.Column("component_verification_snapshot", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    """Remove component configuration while leaving earlier rental schema intact."""
    op.drop_column("rental_order_items", "component_verification_snapshot")
    op.drop_index(
        "ix_rental_asset_components_is_active", table_name="rental_asset_components"
    )
    op.drop_index(
        "ix_rental_asset_components_rental_asset_id",
        table_name="rental_asset_components",
    )
    op.drop_table("rental_asset_components")
