"""Allow RentalAssets allocated after Intake.

Revision ID: 0026_rental_allocation
Revises: 0025_catalog_prices
"""

from alembic import op

revision: str = "0026_rental_allocation"
down_revision: str | None = "0025_catalog_prices"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.alter_column("rental_assets", "intake_item_id", nullable=True)


def downgrade() -> None:
    op.alter_column("rental_assets", "intake_item_id", nullable=False)
