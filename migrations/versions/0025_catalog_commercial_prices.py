"""Add catalog rental commercial price types.

Revision ID: 0025_catalog_prices
Revises: 0024_rental_economics
Create Date: 2026-08-14 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0025_catalog_prices"
down_revision: str | None = "0024_rental_economics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Extend the immutable price history with rental catalog meanings."""
    op.execute("ALTER TYPE price_type ADD VALUE IF NOT EXISTS 'rental'")
    op.execute("ALTER TYPE price_type ADD VALUE IF NOT EXISTS 'rental_deposit'")


def downgrade() -> None:
    """Keep enum values because PostgreSQL cannot safely remove them in place."""

