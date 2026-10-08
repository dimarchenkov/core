"""Add cash payment facts and nullable acquiring ownership.

Revision ID: 0036_cash_payment
Revises: 0035_sales_discount_manual
Create Date: 2026-10-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0036_cash_payment"
down_revision: str | None = "0035_sales_discount_manual"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Allow provider-free successful cash PaymentAttempts."""
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE payment_method ADD VALUE IF NOT EXISTS 'cash'")
    op.alter_column("payment_attempts", "integration_id", existing_type=sa.UUID(), nullable=True)
    op.alter_column(
        "payment_attempts",
        "provider",
        existing_type=sa.Enum(name="integration_provider"),
        nullable=True,
    )
    op.create_check_constraint(
        "ck_payment_attempts_method_provider",
        "payment_attempts",
        "(payment_method = 'cash' AND integration_id IS NULL AND provider IS NULL) OR "
        "(payment_method = 'card' AND integration_id IS NOT NULL AND provider IS NOT NULL)",
    )
    op.execute(
        """
        UPDATE integrations
        SET configuration = jsonb_set(
            configuration::jsonb,
            '{acquiring_mode}',
            '"sbp_with_card"'::jsonb,
            true
        )::json
        WHERE provider = 'aqsi'
          AND NOT (configuration::jsonb ? 'acquiring_mode')
        """
    )


def downgrade() -> None:
    """Restore mandatory acquiring ownership when no cash facts exist."""
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM payment_attempts WHERE payment_method = 'cash'
            ) THEN
                RAISE EXCEPTION 'Cannot downgrade while cash payment attempts exist';
            END IF;
        END
        $$
        """
    )
    op.execute(
        """
        UPDATE integrations
        SET configuration = (configuration::jsonb - 'acquiring_mode')::json
        WHERE provider = 'aqsi'
        """
    )
    op.drop_constraint(
        "ck_payment_attempts_method_provider",
        "payment_attempts",
        type_="check",
    )
    op.alter_column(
        "payment_attempts",
        "provider",
        existing_type=sa.Enum(name="integration_provider"),
        nullable=False,
    )
    op.alter_column("payment_attempts", "integration_id", existing_type=sa.UUID(), nullable=False)
