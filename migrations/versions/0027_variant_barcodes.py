"""Add typed multi-barcode assignments for CatalogVariant.

Revision ID: 0027_variant_barcodes
Revises: 0026_rental_allocation
Create Date: 2026-08-29 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0027_variant_barcodes"
down_revision: str | None = "0026_rental_allocation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Preserve every legacy barcode as INTERNAL, then enable manufacturer codes."""
    barcode_source = postgresql.ENUM(
        "internal",
        "manufacturer",
        name="barcode_source",
        create_type=False,
    )
    barcode_source.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "catalog_variant_barcodes",
        sa.Column("variant_id", sa.Uuid(), nullable=False),
        sa.Column("value", sa.String(length=128), nullable=False),
        sa.Column("source", barcode_source, nullable=False),
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
        sa.ForeignKeyConstraint(["variant_id"], ["catalog_variants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["deleted_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("value", name="uq_catalog_variant_barcodes_value"),
    )
    op.create_index(
        op.f("ix_catalog_variant_barcodes_variant_id"),
        "catalog_variant_barcodes",
        ["variant_id"],
    )
    op.create_index(
        op.f("ix_catalog_variant_barcodes_source"),
        "catalog_variant_barcodes",
        ["source"],
    )
    op.execute(
        """
        INSERT INTO catalog_variant_barcodes (
            id, variant_id, value, source, created_at, updated_at,
            version, created_by_id, updated_by_id
        )
        SELECT
            gen_random_uuid(), id, barcode, 'internal', created_at, updated_at,
            1, created_by_id, updated_by_id
        FROM catalog_variants
        """
    )
    op.add_column(
        "intake_item_drafts",
        sa.Column("manufacturer_barcode", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    """Remove additional codes while preserving the legacy primary INTERNAL field."""
    op.drop_column("intake_item_drafts", "manufacturer_barcode")
    op.drop_index(
        op.f("ix_catalog_variant_barcodes_source"),
        table_name="catalog_variant_barcodes",
    )
    op.drop_index(
        op.f("ix_catalog_variant_barcodes_variant_id"),
        table_name="catalog_variant_barcodes",
    )
    op.drop_table("catalog_variant_barcodes")
    postgresql.ENUM(name="barcode_source").drop(op.get_bind(), checkfirst=True)
