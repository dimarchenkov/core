"""Add compact emergency incident resolution choices.

Revision ID: 0056_emergency_choices
Revises: 0055_incident_resolutions
Create Date: 2026-10-28
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0056_emergency_choices"
down_revision: str | None = "0055_incident_resolutions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Extend the existing incident-resolution type without changing old facts."""
    for value in (
        "external_pos_payment_confirmed",
        "original_operation_canceled_confirmed",
        "manual_review_confirmed",
    ):
        op.execute(f"ALTER TYPE incident_resolution_type ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    """PostgreSQL enum values remain for compatibility with retained audit rows."""
