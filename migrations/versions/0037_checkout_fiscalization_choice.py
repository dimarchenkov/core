"""Persist per-checkout fiscalization choice and intentional skip.

Revision ID: 0037_checkout_fiscal_choice
Revises: 0036_cash_payment
Create Date: 2026-10-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0037_checkout_fiscal_choice"
down_revision: str | None = "0036_cash_payment"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add required/skipped fiscal intent without a global setting."""
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE fiscalization_status ADD VALUE IF NOT EXISTS 'skipped'")

    op.add_column(
        "payment_attempts",
        sa.Column(
            "fiscalization_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.create_check_constraint(
        "ck_payment_attempts_card_requires_fiscalization",
        "payment_attempts",
        "payment_method != 'card' OR fiscalization_required",
    )
    op.alter_column("fiscalizations", "integration_id", existing_type=sa.UUID(), nullable=True)
    op.alter_column(
        "fiscalizations",
        "provider",
        existing_type=sa.Enum(name="integration_provider"),
        nullable=True,
    )
    op.create_check_constraint(
        "ck_fiscalizations_status_provider",
        "fiscalizations",
        "(status = 'skipped' AND integration_id IS NULL AND provider IS NULL) OR "
        "(status != 'skipped' AND integration_id IS NOT NULL AND provider IS NOT NULL)",
    )


def downgrade() -> None:
    """Restore mandatory fiscal provider ownership when no skipped facts exist."""
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM fiscalizations WHERE status = 'skipped') THEN
                RAISE EXCEPTION 'Cannot downgrade while skipped fiscalizations exist';
            END IF;
        END
        $$
        """
    )
    op.drop_constraint(
        "ck_fiscalizations_status_provider",
        "fiscalizations",
        type_="check",
    )
    op.alter_column(
        "fiscalizations",
        "provider",
        existing_type=sa.Enum(name="integration_provider"),
        nullable=False,
    )
    op.alter_column("fiscalizations", "integration_id", existing_type=sa.UUID(), nullable=False)
    op.drop_constraint(
        "ck_payment_attempts_card_requires_fiscalization",
        "payment_attempts",
        type_="check",
    )
    op.drop_column("payment_attempts", "fiscalization_required")
