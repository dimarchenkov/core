"""Create catalog variants table.

Revision ID: 0004_create_catalog_variants
Revises: 0003_create_catalog_products
Create Date: 2026-07-10 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_create_catalog_variants"
down_revision: str | None = "0003_create_catalog_products"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create catalog variants and the sequence used for generated SKUs."""
    op.execute("CREATE SEQUENCE catalog_variant_sku_seq START WITH 1")
    op.create_table(
        "catalog_variants",
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("barcode", sa.String(length=64), nullable=True),
        sa.Column("attributes", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
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
        sa.ForeignKeyConstraint(["product_id"], ["catalog_products.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sku"),
    )
    op.create_index(op.f("ix_catalog_variants_product_id"), "catalog_variants", ["product_id"])
    op.create_index(op.f("ix_catalog_variants_sku"), "catalog_variants", ["sku"])


def downgrade() -> None:
    """Drop catalog variants and their SKU sequence."""
    op.drop_index(op.f("ix_catalog_variants_sku"), table_name="catalog_variants")
    op.drop_index(op.f("ix_catalog_variants_product_id"), table_name="catalog_variants")
    op.drop_table("catalog_variants")
    op.execute("DROP SEQUENCE catalog_variant_sku_seq")
