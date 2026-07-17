"""Add reversal inventory movement type.

Revision ID: 0011_add_reversal_movement
Revises: 0010_stock_movements
Create Date: 2026-07-17 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0011_add_reversal_movement"
down_revision: str | None = "0010_stock_movements"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the immutable reversal value to PostgreSQL's movement type enum."""
    op.execute("ALTER TYPE movement_type ADD VALUE IF NOT EXISTS 'reversal'")


def downgrade() -> None:
    """Keep the PostgreSQL enum value because removing enum values is unsafe."""
