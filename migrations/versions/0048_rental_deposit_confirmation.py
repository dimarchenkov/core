"""Preserve unconfirmed versus explicit-zero rental deposit receipts.

Revision ID: 0048_rental_deposit_confirmation
Revises: 0047_rental_asset_identification
Create Date: 2026-10-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0048_rental_deposit_confirmation"
down_revision: str | None = "0047_rental_asset_identification"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Allow NULL until an operator confirms the actual deposit receipt."""
    op.alter_column(
        "rental_orders",
        "deposit_amount",
        existing_type=sa.Numeric(precision=12, scale=2),
        nullable=True,
    )


def downgrade() -> None:
    """Restore the former non-null storage, mapping unconfirmed history to zero."""
    op.execute("UPDATE rental_orders SET deposit_amount = 0 WHERE deposit_amount IS NULL")
    op.alter_column(
        "rental_orders",
        "deposit_amount",
        existing_type=sa.Numeric(precision=12, scale=2),
        nullable=False,
    )
