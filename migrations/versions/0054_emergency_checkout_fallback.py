"""Add emergency external-POS audit event values.

Revision ID: 0054_emergency_checkout_fallback
Revises: 0053_financial_operation_journal
Create Date: 2026-10-26
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0054_emergency_checkout_fallback"
down_revision: str | None = "0053_financial_operation_journal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Extend the append-only journal vocabulary without rewriting financial history."""
    for value in (
        "emergency_fallback_requested",
        "emergency_fallback_confirmed",
        "external_payment_recorded",
        "late_provider_approval_detected",
    ):
        op.execute(f"ALTER TYPE financial_event_type ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    """Keep PostgreSQL enum values because removing them would rewrite audit data."""
    pass
