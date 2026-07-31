"""Add RentalAsset maintenance, damage, and condition-photo journals.

Revision ID: 0023_rental_history
Revises: 0022_intake_retail_price
Create Date: 2026-07-31 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_rental_history"
down_revision: str | None = "0022_intake_retail_price"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _base_columns() -> list[sa.Column]:
    """Return columns shared by append-only journal records."""
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("updated_by_id", sa.Uuid(), nullable=True),
    ]


def _audit_foreign_keys() -> list[sa.ForeignKeyConstraint]:
    """Return standard nullable audit actor references."""
    return [
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["deleted_by_id"], ["users.id"], ondelete="SET NULL"),
    ]


def upgrade() -> None:
    """Create append-only operational journals for each physical rental item."""
    op.create_table(
        "rental_maintenance_records",
        sa.Column("rental_asset_id", sa.Uuid(), nullable=False),
        sa.Column("performed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("service_type", sa.String(length=64), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("performer_id", sa.Uuid(), nullable=True),
        sa.Column("result", sa.Text(), nullable=False),
        *_base_columns(),
        sa.ForeignKeyConstraint(["rental_asset_id"], ["rental_assets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["performer_id"], ["users.id"], ondelete="SET NULL"),
        *_audit_foreign_keys(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_rental_maintenance_records_rental_asset_id"),
        "rental_maintenance_records",
        ["rental_asset_id"],
    )
    op.create_index(
        op.f("ix_rental_maintenance_records_service_type"),
        "rental_maintenance_records",
        ["service_type"],
    )
    op.create_index(
        op.f("ix_rental_maintenance_records_performer_id"),
        "rental_maintenance_records",
        ["performer_id"],
    )

    op.create_table(
        "rental_damage_records",
        sa.Column("rental_asset_id", sa.Uuid(), nullable=False),
        sa.Column("order_item_id", sa.Uuid(), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("recorded_by_id", sa.Uuid(), nullable=True),
        *_base_columns(),
        sa.ForeignKeyConstraint(["rental_asset_id"], ["rental_assets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["order_item_id"], ["rental_order_items.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["recorded_by_id"], ["users.id"], ondelete="SET NULL"),
        *_audit_foreign_keys(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_rental_damage_records_rental_asset_id"),
        "rental_damage_records",
        ["rental_asset_id"],
    )
    op.create_index(
        op.f("ix_rental_damage_records_order_item_id"), "rental_damage_records", ["order_item_id"]
    )
    op.create_index(
        op.f("ix_rental_damage_records_severity"), "rental_damage_records", ["severity"]
    )
    op.create_index(
        op.f("ix_rental_damage_records_recorded_by_id"), "rental_damage_records", ["recorded_by_id"]
    )

    op.create_table(
        "rental_condition_photos",
        sa.Column("rental_asset_id", sa.Uuid(), nullable=False),
        sa.Column("order_item_id", sa.Uuid(), nullable=True),
        sa.Column("image_id", sa.Uuid(), nullable=False),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("recorded_by_id", sa.Uuid(), nullable=True),
        *_base_columns(),
        sa.ForeignKeyConstraint(["rental_asset_id"], ["rental_assets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["order_item_id"], ["rental_order_items.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["image_id"], ["images.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["recorded_by_id"], ["users.id"], ondelete="SET NULL"),
        *_audit_foreign_keys(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("image_id"),
    )
    op.create_index(
        op.f("ix_rental_condition_photos_rental_asset_id"),
        "rental_condition_photos",
        ["rental_asset_id"],
    )
    op.create_index(
        op.f("ix_rental_condition_photos_order_item_id"),
        "rental_condition_photos",
        ["order_item_id"],
    )
    op.create_index(op.f("ix_rental_condition_photos_stage"), "rental_condition_photos", ["stage"])
    op.create_index(
        op.f("ix_rental_condition_photos_recorded_by_id"),
        "rental_condition_photos",
        ["recorded_by_id"],
    )


def downgrade() -> None:
    """Remove RentalAsset lifecycle journals in reverse dependency order."""
    for table, indexes in (
        (
            "rental_condition_photos",
            ["recorded_by_id", "stage", "order_item_id", "rental_asset_id"],
        ),
        (
            "rental_damage_records",
            ["recorded_by_id", "severity", "order_item_id", "rental_asset_id"],
        ),
        (
            "rental_maintenance_records",
            ["performer_id", "service_type", "rental_asset_id"],
        ),
    ):
        for suffix in indexes:
            op.drop_index(op.f(f"ix_{table}_{suffix}"), table_name=table)
        op.drop_table(table)
