"""Record the issuing operator on each rental order item.

Revision ID: 0042_rental_item_issue_audit
Revises: 0041_duration_issue_timing
Create Date: 2026-10-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0042_rental_item_issue_audit"
down_revision: str | None = "0041_duration_issue_timing"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add nullable item-level issue attribution and backfill historical issues."""
    op.add_column("rental_order_items", sa.Column("issued_by_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_rental_order_items_issued_by_id_users",
        "rental_order_items",
        "users",
        ["issued_by_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute(
        """
        UPDATE rental_order_items AS item
        SET issued_by_id = orders.issued_by_id
        FROM rental_orders AS orders
        WHERE orders.id = item.order_id
          AND item.issued_at IS NOT NULL
        """
    )


def downgrade() -> None:
    """Remove item-level issue attribution."""
    op.drop_constraint(
        "fk_rental_order_items_issued_by_id_users",
        "rental_order_items",
        type_="foreignkey",
    )
    op.drop_column("rental_order_items", "issued_by_id")
