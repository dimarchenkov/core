"""Generate and protect primary internal variant barcodes.

Revision ID: 0013_generate_barcodes
Revises: 0012_create_prices
Create Date: 2026-07-18 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_generate_barcodes"
down_revision: str | None = "0012_create_prices"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Backfill missing barcodes before enforcing stable uniqueness."""
    op.execute(
        """
        WITH barcode_bodies AS (
            SELECT id, '20' || lpad(substring(sku FROM 5), 10, '0') AS body
            FROM catalog_variants
            WHERE barcode IS NULL
        ),
        barcode_values AS (
            SELECT
                id,
                body,
                (
                    SELECT sum(
                        substring(body FROM position FOR 1)::integer
                        * CASE WHEN position % 2 = 1 THEN 1 ELSE 3 END
                    )
                    FROM generate_series(1, 12) AS position
                ) AS weighted_sum
            FROM barcode_bodies
        )
        UPDATE catalog_variants AS variant
        SET barcode = value.body || ((10 - value.weighted_sum % 10) % 10)::text
        FROM barcode_values AS value
        WHERE variant.id = value.id
        """
    )

    op.alter_column(
        "catalog_variants",
        "barcode",
        existing_type=sa.String(length=64),
        type_=sa.String(length=22),
        nullable=False,
    )
    op.create_unique_constraint("uq_catalog_variants_barcode", "catalog_variants", ["barcode"])
    op.create_index(op.f("ix_catalog_variants_barcode"), "catalog_variants", ["barcode"])


def downgrade() -> None:
    """Restore the earlier optional and manually editable barcode storage shape."""
    op.drop_index(op.f("ix_catalog_variants_barcode"), table_name="catalog_variants")
    op.drop_constraint("uq_catalog_variants_barcode", "catalog_variants", type_="unique")
    op.alter_column(
        "catalog_variants",
        "barcode",
        existing_type=sa.String(length=22),
        type_=sa.String(length=64),
        nullable=True,
    )
