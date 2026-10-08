"""Add durable checkout, payment and fiscalization state.

Revision ID: 0034_sales_checkout
Revises: 0033_sales_workspace
Create Date: 2026-10-08 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0034_sales_checkout"
down_revision: str | None = "0033_sales_workspace"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

payment_method = postgresql.ENUM("card", name="payment_method", create_type=False)
payment_status = postgresql.ENUM(
    "pending", "unknown", "succeeded", "failed", name="payment_status", create_type=False
)
fiscalization_status = postgresql.ENUM(
    "pending",
    "unknown",
    "succeeded",
    "failed",
    name="fiscalization_status",
    create_type=False,
)


def _base_columns() -> list[sa.Column[object]]:
    """Return shared entity columns for checkout-owned records."""
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("updated_by_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["deleted_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    ]


def upgrade() -> None:
    """Extend Sale and create separate acquiring/fiscal records."""
    for value in (
        "payment_pending",
        "paid",
        "fiscalization_pending",
        "completed",
        "payment_failed",
        "fiscalization_failed",
    ):
        op.execute(f"ALTER TYPE sale_status ADD VALUE IF NOT EXISTS '{value}'")
    payment_method.create(op.get_bind(), checkfirst=True)
    payment_status.create(op.get_bind(), checkfirst=True)
    fiscalization_status.create(op.get_bind(), checkfirst=True)
    op.add_column("sales", sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "sales", sa.Column("inventory_posted_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("sales", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "payment_attempts",
        *_base_columns(),
        sa.Column("sale_id", sa.Uuid(), nullable=False),
        sa.Column("integration_id", sa.Uuid(), nullable=False),
        sa.Column(
            "provider",
            postgresql.ENUM(name="integration_provider", create_type=False),
            nullable=False,
        ),
        sa.Column("payment_method", payment_method, nullable=False),
        sa.Column("requested_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), server_default="RUB", nullable=False),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.Column("status", payment_status, server_default="pending", nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("provider_metadata", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("error_message", sa.String(1000), nullable=True),
        sa.CheckConstraint("requested_amount > 0", name="ck_payment_attempts_amount_positive"),
        sa.ForeignKeyConstraint(["integration_id"], ["integrations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["sale_id"], ["sales.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("idempotency_key", name="uq_payment_attempts_idempotency_key"),
        sa.UniqueConstraint("sale_id", "attempt_number", name="uq_payment_attempts_sale_number"),
    )
    op.create_index("ix_payment_attempts_sale_id", "payment_attempts", ["sale_id"])
    op.create_index("ix_payment_attempts_integration_id", "payment_attempts", ["integration_id"])
    op.create_index("ix_payment_attempts_external_id", "payment_attempts", ["external_id"])
    op.create_index("ix_payment_attempts_status", "payment_attempts", ["status"])
    op.create_table(
        "fiscalizations",
        *_base_columns(),
        sa.Column("sale_id", sa.Uuid(), nullable=False),
        sa.Column("payment_attempt_id", sa.Uuid(), nullable=False),
        sa.Column("integration_id", sa.Uuid(), nullable=False),
        sa.Column(
            "provider",
            postgresql.ENUM(name="integration_provider", create_type=False),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.Column("external_receipt_id", sa.String(255), nullable=True),
        sa.Column("status", fiscalization_status, server_default="pending", nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="1", nullable=False),
        sa.Column("fiscal_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), server_default="RUB", nullable=False),
        sa.Column("provider_metadata", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("error_message", sa.String(1000), nullable=True),
        sa.CheckConstraint("fiscal_amount > 0", name="ck_fiscalizations_amount_positive"),
        sa.ForeignKeyConstraint(["integration_id"], ["integrations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["payment_attempt_id"], ["payment_attempts.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["sale_id"], ["sales.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("idempotency_key", name="uq_fiscalizations_idempotency_key"),
        sa.UniqueConstraint("payment_attempt_id"),
        sa.UniqueConstraint("sale_id", name="uq_fiscalizations_sale_id"),
    )
    op.create_index("ix_fiscalizations_sale_id", "fiscalizations", ["sale_id"])
    op.create_index("ix_fiscalizations_integration_id", "fiscalizations", ["integration_id"])
    op.create_index("ix_fiscalizations_external_id", "fiscalizations", ["external_id"])
    op.create_index("ix_fiscalizations_status", "fiscalizations", ["status"])
    op.create_index(
        "uq_stock_movements_sale_source_variant",
        "stock_movements",
        ["source_id", "variant_id"],
        unique=True,
        postgresql_where=sa.text("source_type = 'sale' AND movement_type = 'sale'"),
    )


def downgrade() -> None:
    """Remove checkout persistence and restore the 5.1 Sale enum."""
    op.drop_index("uq_stock_movements_sale_source_variant", table_name="stock_movements")
    op.drop_index("ix_fiscalizations_status", table_name="fiscalizations")
    op.drop_index("ix_fiscalizations_external_id", table_name="fiscalizations")
    op.drop_index("ix_fiscalizations_integration_id", table_name="fiscalizations")
    op.drop_index("ix_fiscalizations_sale_id", table_name="fiscalizations")
    op.drop_table("fiscalizations")
    op.drop_index("ix_payment_attempts_status", table_name="payment_attempts")
    op.drop_index("ix_payment_attempts_external_id", table_name="payment_attempts")
    op.drop_index("ix_payment_attempts_integration_id", table_name="payment_attempts")
    op.drop_index("ix_payment_attempts_sale_id", table_name="payment_attempts")
    op.drop_table("payment_attempts")
    op.drop_column("sales", "completed_at")
    op.drop_column("sales", "inventory_posted_at")
    op.drop_column("sales", "paid_at")
    fiscalization_status.drop(op.get_bind(), checkfirst=True)
    payment_status.drop(op.get_bind(), checkfirst=True)
    payment_method.drop(op.get_bind(), checkfirst=True)
    op.execute("ALTER TABLE sales ALTER COLUMN status DROP DEFAULT")
    op.execute("ALTER TABLE sales ALTER COLUMN status TYPE text USING status::text")
    op.execute("DROP TYPE sale_status")
    op.execute("CREATE TYPE sale_status AS ENUM ('draft', 'cancelled')")
    op.execute("ALTER TABLE sales ALTER COLUMN status TYPE sale_status USING status::sale_status")
    op.execute("ALTER TABLE sales ALTER COLUMN status SET DEFAULT 'draft'")
