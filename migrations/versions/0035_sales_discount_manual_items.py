"""Add receipt discount, manual SaleItems and canceled payment outcome.

Revision ID: 0035_sales_discount_manual
Revises: 0034_sales_checkout
Create Date: 2026-10-08 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0035_sales_discount_manual"
down_revision: str | None = "0034_sales_checkout"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

sale_discount_type = postgresql.ENUM("percent", name="sale_discount_type", create_type=False)
sale_item_source = postgresql.ENUM("catalog", "manual", name="sale_item_source", create_type=False)


def upgrade() -> None:
    """Persist discount allocation, manual items and terminal cancellation distinctly."""
    op.execute("ALTER TYPE payment_status ADD VALUE IF NOT EXISTS 'canceled'")
    sale_discount_type.create(op.get_bind(), checkfirst=True)
    sale_item_source.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "sales",
        sa.Column("subtotal_amount", sa.Numeric(12, 2), server_default="0.00", nullable=False),
    )
    op.add_column(
        "sales",
        sa.Column(
            "discount_type",
            sale_discount_type,
            server_default="percent",
            nullable=False,
        ),
    )
    op.add_column(
        "sales",
        sa.Column("discount_value", sa.Numeric(5, 2), server_default="0.00", nullable=False),
    )
    op.add_column(
        "sales",
        sa.Column("discount_amount", sa.Numeric(12, 2), server_default="0.00", nullable=False),
    )
    op.execute("UPDATE sales SET subtotal_amount = total_amount")
    op.create_check_constraint(
        "ck_sales_subtotal_nonnegative", "sales", "subtotal_amount >= 0"
    )
    op.create_check_constraint(
        "ck_sales_discount_value_range",
        "sales",
        "discount_value >= 0 AND discount_value <= 99.99",
    )
    op.create_check_constraint(
        "ck_sales_discount_amount_nonnegative", "sales", "discount_amount >= 0"
    )
    op.create_check_constraint(
        "ck_sales_total_arithmetic",
        "sales",
        "total_amount = subtotal_amount - discount_amount",
    )

    op.add_column(
        "sale_items",
        sa.Column("source", sale_item_source, server_default="catalog", nullable=False),
    )
    op.add_column(
        "sale_items",
        sa.Column("fiscal_line_total", sa.Numeric(12, 2), server_default="0.00", nullable=False),
    )
    op.add_column(
        "sale_items",
        sa.Column("fiscal_allocations", sa.JSON(), server_default="[]", nullable=False),
    )
    op.execute(
        """
        UPDATE sale_items
        SET fiscal_line_total = line_total,
            fiscal_allocations = json_build_array(
                json_build_object('unit_price', unit_price::text, 'quantity', quantity)
            )
        """
    )
    op.alter_column("sale_items", "variant_id", existing_type=sa.Uuid(), nullable=True)
    for column, size in (
        ("product_title_snapshot", 255),
        ("variant_title_snapshot", 255),
        ("sku_snapshot", 64),
        ("barcode_snapshot", 128),
    ):
        op.alter_column(
            "sale_items", column, existing_type=sa.String(size), nullable=True
        )
    op.create_index("ix_sale_items_source", "sale_items", ["source"])
    op.create_check_constraint(
        "ck_sale_items_fiscal_line_total_nonnegative",
        "sale_items",
        "fiscal_line_total >= 0",
    )
    op.create_check_constraint(
        "ck_sale_items_source_variant",
        "sale_items",
        "(source = 'catalog' AND variant_id IS NOT NULL) OR "
        "(source = 'manual' AND variant_id IS NULL)",
    )


def downgrade() -> None:
    """Remove discount/manual-item fields and fold canceled attempts into failed."""
    op.drop_constraint("ck_sale_items_source_variant", "sale_items", type_="check")
    op.drop_constraint(
        "ck_sale_items_fiscal_line_total_nonnegative", "sale_items", type_="check"
    )
    op.drop_index("ix_sale_items_source", table_name="sale_items")
    op.execute("DELETE FROM sale_items WHERE source = 'manual'")
    for column, size in (
        ("barcode_snapshot", 128),
        ("sku_snapshot", 64),
        ("variant_title_snapshot", 255),
        ("product_title_snapshot", 255),
    ):
        op.alter_column(
            "sale_items", column, existing_type=sa.String(size), nullable=False
        )
    op.alter_column("sale_items", "variant_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_column("sale_items", "fiscal_allocations")
    op.drop_column("sale_items", "fiscal_line_total")
    op.drop_column("sale_items", "source")
    sale_item_source.drop(op.get_bind(), checkfirst=True)

    op.drop_constraint("ck_sales_total_arithmetic", "sales", type_="check")
    op.drop_constraint("ck_sales_discount_amount_nonnegative", "sales", type_="check")
    op.drop_constraint("ck_sales_discount_value_range", "sales", type_="check")
    op.drop_constraint("ck_sales_subtotal_nonnegative", "sales", type_="check")
    op.drop_column("sales", "discount_amount")
    op.drop_column("sales", "discount_value")
    op.drop_column("sales", "discount_type")
    op.drop_column("sales", "subtotal_amount")
    sale_discount_type.drop(op.get_bind(), checkfirst=True)

    op.execute("UPDATE payment_attempts SET status = 'failed' WHERE status = 'canceled'")
    op.execute("ALTER TABLE payment_attempts ALTER COLUMN status DROP DEFAULT")
    op.execute("ALTER TABLE payment_attempts ALTER COLUMN status TYPE text USING status::text")
    op.execute("DROP TYPE payment_status")
    op.execute(
        "CREATE TYPE payment_status AS ENUM "
        "('pending', 'unknown', 'succeeded', 'failed')"
    )
    op.execute(
        "ALTER TABLE payment_attempts ALTER COLUMN status TYPE payment_status "
        "USING status::payment_status"
    )
    op.execute("ALTER TABLE payment_attempts ALTER COLUMN status SET DEFAULT 'pending'")
