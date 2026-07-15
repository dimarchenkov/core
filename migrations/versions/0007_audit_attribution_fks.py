"""Add user attribution foreign keys to shared audit columns.

Revision ID: 0007_audit_attribution_fks
Revises: 0006_create_identity_foundation
Create Date: 2026-07-15 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007_audit_attribution_fks"
down_revision: str | None = "0006_create_identity_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (
    "categories",
    "catalog_products",
    "catalog_variants",
    "images",
    "image_links",
    "users",
)
_ATTRIBUTION_COLUMNS = ("created_by_id", "updated_by_id", "deleted_by_id")


def _constraint_name(table_name: str, column_name: str) -> str:
    """Return the stable name for one attribution foreign key."""
    return f"fk_{table_name}_{column_name}_users"


def upgrade() -> None:
    """Add nullable user attribution foreign keys to existing shared columns."""
    for table_name in _TABLES:
        for column_name in _ATTRIBUTION_COLUMNS:
            op.create_foreign_key(
                _constraint_name(table_name, column_name),
                table_name,
                "users",
                [column_name],
                ["id"],
                ondelete="SET NULL",
            )


def downgrade() -> None:
    """Remove user attribution foreign keys while preserving their columns."""
    for table_name in reversed(_TABLES):
        for column_name in _ATTRIBUTION_COLUMNS:
            op.drop_constraint(
                _constraint_name(table_name, column_name),
                table_name,
                type_="foreignkey",
            )
