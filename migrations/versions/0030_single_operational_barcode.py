"""Make one operational barcode authoritative for each CatalogVariant.

Revision ID: 0030_single_operational_barcode
Revises: 0029_intake_label_identity
Create Date: 2026-09-10 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0030_single_operational_barcode"
down_revision: str | None = "0029_intake_label_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Prefer one unambiguous external code and retire every other active alias."""
    barcode_source = postgresql.ENUM(
        "internal", "manufacturer", name="barcode_source", create_type=False
    )
    op.alter_column(
        "catalog_variants",
        "barcode",
        existing_type=sa.String(length=22),
        type_=sa.String(length=128),
        existing_nullable=False,
    )
    op.add_column(
        "catalog_variants",
        sa.Column("barcode_source", barcode_source, nullable=True),
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM catalog_variant_barcodes
            WHERE deleted_at IS NULL AND source = 'manufacturer'
            GROUP BY variant_id
            HAVING count(*) > 1
          ) THEN
            RAISE EXCEPTION
              'Ambiguous migration: Variant has multiple active manufacturer barcodes';
          END IF;
        END $$
        """
    )
    op.execute(
        """
        UPDATE catalog_variants AS variant
        SET barcode = external.value, barcode_source = 'manufacturer'
        FROM catalog_variant_barcodes AS external
        WHERE external.variant_id = variant.id
          AND external.source = 'manufacturer'
          AND external.deleted_at IS NULL
        """
    )
    op.execute(
        "UPDATE catalog_variants SET barcode_source = 'internal' WHERE barcode_source IS NULL"
    )
    op.execute(
        """
        UPDATE catalog_variant_barcodes AS history
        SET deleted_at = now(), updated_at = now(), version = history.version + 1
        FROM catalog_variants AS variant
        WHERE history.variant_id = variant.id
          AND history.deleted_at IS NULL
          AND history.value <> variant.barcode
        """
    )
    op.execute(
        """
        UPDATE catalog_variant_barcodes AS history
        SET source = variant.barcode_source,
            updated_at = now(),
            version = history.version + 1
        FROM catalog_variants AS variant
        WHERE history.variant_id = variant.id
          AND history.value = variant.barcode
          AND history.deleted_at IS NULL
          AND history.source <> variant.barcode_source
        """
    )
    op.execute(
        """
        INSERT INTO catalog_variant_barcodes (
          id, variant_id, value, source, created_at, updated_at, version
        )
        SELECT gen_random_uuid(), variant.id, variant.barcode,
               variant.barcode_source, now(), now(), 1
        FROM catalog_variants AS variant
        WHERE NOT EXISTS (
          SELECT 1 FROM catalog_variant_barcodes AS history
          WHERE history.variant_id = variant.id AND history.deleted_at IS NULL
        )
        """
    )
    op.alter_column(
        "catalog_variants",
        "barcode_source",
        existing_type=barcode_source,
        nullable=False,
        server_default="internal",
    )
    op.create_index(
        "uq_catalog_variant_barcodes_one_active",
        "catalog_variant_barcodes",
        ["variant_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    """Restore the former internal primary plus active-alias representation when possible."""
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM catalog_variants AS variant
            WHERE NOT EXISTS (
              SELECT 1 FROM catalog_variant_barcodes AS history
              WHERE history.variant_id = variant.id AND history.source = 'internal'
            )
          ) THEN
            RAISE EXCEPTION 'Cannot downgrade: Variant has no historical internal barcode';
          END IF;
        END $$
        """
    )
    op.drop_index(
        "uq_catalog_variant_barcodes_one_active",
        table_name="catalog_variant_barcodes",
    )
    op.execute(
        """
        UPDATE catalog_variant_barcodes
        SET deleted_at = NULL
        WHERE source = 'internal'
        """
    )
    op.execute(
        """
        UPDATE catalog_variants AS variant
        SET barcode = history.value
        FROM catalog_variant_barcodes AS history
        WHERE history.variant_id = variant.id
          AND history.source = 'internal'
          AND history.created_at = (
            SELECT min(candidate.created_at)
            FROM catalog_variant_barcodes AS candidate
            WHERE candidate.variant_id = variant.id AND candidate.source = 'internal'
          )
        """
    )
    op.drop_column("catalog_variants", "barcode_source")
    op.alter_column(
        "catalog_variants",
        "barcode",
        existing_type=sa.String(length=128),
        type_=sa.String(length=22),
        existing_nullable=False,
    )
