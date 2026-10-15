"""Add optional Product-local display order to Catalog Variants.

Revision ID: 0049_variant_sort_order
Revises: 0048_rental_deposit_confirmation
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0049_variant_sort_order"
down_revision: str | None = "0048_rental_deposit_confirmation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable ordering without changing legacy alphabetical behavior."""
    op.add_column("catalog_variants", sa.Column("sort_order", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Remove Product-local Variant ordering."""
    op.drop_column("catalog_variants", "sort_order")
