"""Add explicit rental checkout operator attribution.

Revision ID: 0020_rental_issue_audit
Revises: 0019_create_rental_orders
Create Date: 2026-07-31 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_rental_issue_audit"
down_revision: str | None = "0019_create_rental_orders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Store the employee who completed checkout independently of later edits."""
    op.add_column("rental_orders", sa.Column("issued_by_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_rental_orders_issued_by_id_users",
        "rental_orders",
        "users",
        ["issued_by_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    """Remove explicit checkout operator attribution."""
    op.drop_constraint(
        "fk_rental_orders_issued_by_id_users",
        "rental_orders",
        type_="foreignkey",
    )
    op.drop_column("rental_orders", "issued_by_id")
