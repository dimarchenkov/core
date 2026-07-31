"""Add optional retail price to intake item drafts.

Revision ID: 0022_intake_retail_price
Revises: 0021_rental_return_audit
Create Date: 2026-07-31 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_intake_retail_price"
down_revision: str | None = "0021_rental_return_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Persist an optional retail price until an IntakeSession is completed."""
    op.add_column("intake_item_drafts", sa.Column("retail_price", sa.Numeric(12, 2)))
    op.create_check_constraint(
        "ck_intake_item_drafts_retail_price_nonnegative",
        "intake_item_drafts",
        "retail_price IS NULL OR retail_price >= 0",
    )


def downgrade() -> None:
    """Remove the optional intake retail price."""
    op.drop_constraint(
        "ck_intake_item_drafts_retail_price_nonnegative",
        "intake_item_drafts",
        type_="check",
    )
    op.drop_column("intake_item_drafts", "retail_price")
