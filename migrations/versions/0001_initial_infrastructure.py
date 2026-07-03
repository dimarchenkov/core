"""Initial infrastructure baseline.

Revision ID: 0001_initial_infrastructure
Revises:
Create Date: 2026-07-03 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "0001_initial_infrastructure"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the initial empty infrastructure baseline."""
    pass


def downgrade() -> None:
    """Revert the initial empty infrastructure baseline."""
    pass
