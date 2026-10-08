"""Create persistent operator-owned Sales carts.

Revision ID: 0033_sales_workspace
Revises: 0032_integration_settings
Create Date: 2026-10-07 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0033_sales_workspace"
down_revision: str | None = "0032_integration_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

sale_status = postgresql.ENUM("draft", "cancelled", name="sale_status", create_type=False)


def _base_columns() -> list[sa.Column[object]]:
    """Return the shared entity columns used by Sales tables."""
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
    """Create Sale and SaleItem persistence without checkout or Inventory effects."""
    sale_status.create(op.get_bind(), checkfirst=True)
    op.execute(sa.schema.CreateSequence(sa.Sequence("sale_number_seq")))
    op.create_table(
        "sales",
        *_base_columns(),
        sa.Column("sale_number", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("status", sale_status, server_default="draft", nullable=False),
        sa.Column("total_amount", sa.Numeric(12, 2), server_default="0.00", nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="RUB", nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint("total_amount >= 0", name="ck_sales_total_amount_nonnegative"),
        sa.ForeignKeyConstraint(["cancelled_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("sale_number"),
    )
    op.create_index("ix_sales_owner_id", "sales", ["owner_id"], unique=False)
    op.create_index("ix_sales_sale_number", "sales", ["sale_number"], unique=True)
    op.create_index("ix_sales_status", "sales", ["status"], unique=False)
    op.create_table(
        "sale_items",
        *_base_columns(),
        sa.Column("sale_id", sa.Uuid(), nullable=False),
        sa.Column("variant_id", sa.Uuid(), nullable=False),
        sa.Column("product_title_snapshot", sa.String(length=255), nullable=False),
        sa.Column("variant_title_snapshot", sa.String(length=255), nullable=False),
        sa.Column("display_label_snapshot", sa.String(length=511), nullable=False),
        sa.Column("sku_snapshot", sa.String(length=64), nullable=False),
        sa.Column("barcode_snapshot", sa.String(length=128), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("quantity", sa.Integer(), server_default="1", nullable=False),
        sa.Column("line_total", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="RUB", nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_sale_items_quantity_positive"),
        sa.CheckConstraint("unit_price >= 0", name="ck_sale_items_unit_price_nonnegative"),
        sa.CheckConstraint("line_total >= 0", name="ck_sale_items_line_total_nonnegative"),
        sa.ForeignKeyConstraint(["sale_id"], ["sales.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["variant_id"], ["catalog_variants.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("sale_id", "variant_id", name="uq_sale_items_sale_variant"),
    )
    op.create_index("ix_sale_items_sale_id", "sale_items", ["sale_id"], unique=False)
    op.create_index("ix_sale_items_variant_id", "sale_items", ["variant_id"], unique=False)


def downgrade() -> None:
    """Remove Sales cart persistence and its implemented-state enum."""
    op.drop_index("ix_sale_items_variant_id", table_name="sale_items")
    op.drop_index("ix_sale_items_sale_id", table_name="sale_items")
    op.drop_table("sale_items")
    op.drop_index("ix_sales_status", table_name="sales")
    op.drop_index("ix_sales_sale_number", table_name="sales")
    op.drop_index("ix_sales_owner_id", table_name="sales")
    op.drop_table("sales")
    op.execute(sa.schema.DropSequence(sa.Sequence("sale_number_seq")))
    postgresql.ENUM(name="sale_status").drop(op.get_bind(), checkfirst=True)
