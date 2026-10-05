"""Add explicit TEST scope to Catalog and workflow aggregate roots.

Revision ID: 0031_test_data_scope
Revises: 0030_single_operational_barcode
Create Date: 2026-10-04 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031_test_data_scope"
down_revision: str | None = "0030_single_operational_barcode"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Default every historical row to production until an admin classifies it."""
    for table in ("catalog_products", "intake_sessions", "receipts"):
        op.add_column(
            table,
            sa.Column("is_test", sa.Boolean(), server_default=sa.false(), nullable=False),
        )
        op.create_index(f"ix_{table}_is_test", table, ["is_test"], unique=False)


def downgrade() -> None:
    """Remove explicit TEST scope markers."""
    for table in ("receipts", "intake_sessions", "catalog_products"):
        op.drop_index(f"ix_{table}_is_test", table_name=table)
        op.drop_column(table, "is_test")
