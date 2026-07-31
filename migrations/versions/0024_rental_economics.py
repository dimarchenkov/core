"""Add immutable acquisition cost and maintenance expense.

Revision ID: 0024_rental_economics
Revises: 0023_rental_history
Create Date: 2026-07-31 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_rental_economics"
down_revision: str | None = "0023_rental_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("rental_assets", sa.Column("acquisition_cost", sa.Numeric(12, 2), nullable=True))
    op.create_check_constraint(
        "ck_rental_assets_acquisition_cost_nonnegative",
        "rental_assets",
        "acquisition_cost IS NULL OR acquisition_cost >= 0",
    )
    op.execute(
        """UPDATE rental_assets AS asset
        SET acquisition_cost = item.purchase_price
        FROM intake_item_drafts AS item
        WHERE item.id = asset.intake_item_id"""
    )
    op.add_column(
        "rental_maintenance_records",
        sa.Column("cost", sa.Numeric(12, 2), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_rental_maintenance_records_cost_nonnegative", "rental_maintenance_records", "cost >= 0"
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_rental_maintenance_records_cost_nonnegative",
        "rental_maintenance_records",
        type_="check",
    )
    op.drop_column("rental_maintenance_records", "cost")
    op.drop_constraint(
        "ck_rental_assets_acquisition_cost_nonnegative", "rental_assets", type_="check"
    )
    op.drop_column("rental_assets", "acquisition_cost")
