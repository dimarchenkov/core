"""Add explicit rental item completion operator attribution.

Revision ID: 0021_rental_return_audit
Revises: 0020_rental_issue_audit
Create Date: 2026-07-31 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_rental_return_audit"
down_revision: str | None = "0020_rental_issue_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Store the employee who completed each returned or lost order item."""
    op.add_column("rental_order_items", sa.Column("completed_by_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_rental_order_items_completed_by_id_users",
        "rental_order_items",
        "users",
        ["completed_by_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    """Remove explicit rental item completion attribution."""
    op.drop_constraint(
        "fk_rental_order_items_completed_by_id_users",
        "rental_order_items",
        type_="foreignkey",
    )
    op.drop_column("rental_order_items", "completed_by_id")
