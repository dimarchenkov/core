"""Create rental orders and aggregate-owned items.

Revision ID: 0019_create_rental_orders
Revises: 0018_create_customers
Create Date: 2026-07-28 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019_create_rental_orders"
down_revision: str | None = "0018_create_customers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _base_columns() -> list[sa.Column]:
    """Return columns shared by aggregate and entity persistence records."""
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("updated_by_id", sa.Uuid(), nullable=True),
    ]


def _audit_foreign_keys() -> list[sa.ForeignKeyConstraint]:
    """Return standard nullable actor references."""
    return [
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["deleted_by_id"], ["users.id"], ondelete="SET NULL"),
    ]


def upgrade() -> None:
    """Create RentalOrder persistence and stable business-number sequence."""
    op.execute("CREATE SEQUENCE rental_order_number_seq START WITH 1")
    order_status = postgresql.ENUM(
        "draft", "issued", "closed", "cancelled",
        name="rental_order_status",
        create_type=False,
    )
    item_status = postgresql.ENUM(
        "prepared", "issued", "returned", "lost", "cancelled",
        name="rental_order_item_status",
        create_type=False,
    )
    order_status.create(op.get_bind(), checkfirst=True)
    item_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "rental_orders",
        sa.Column("order_number", sa.String(length=32), nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=False),
        sa.Column("customer_name_snapshot", sa.String(length=255), nullable=False),
        sa.Column("customer_phone_snapshot", sa.String(length=32), nullable=False),
        sa.Column("status", order_status, nullable=False),
        sa.Column("planned_start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("planned_return_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deposit_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        *_base_columns(),
        sa.CheckConstraint("deposit_amount >= 0", name="ck_rental_orders_deposit"),
        sa.CheckConstraint(
            "planned_return_at > planned_start_at",
            name="ck_rental_orders_period",
        ),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="RESTRICT"),
        *_audit_foreign_keys(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_rental_orders_order_number"),
        "rental_orders",
        ["order_number"],
        unique=True,
    )
    op.create_index(op.f("ix_rental_orders_customer_id"), "rental_orders", ["customer_id"])
    op.create_index(op.f("ix_rental_orders_status"), "rental_orders", ["status"])

    op.create_table(
        "rental_order_items",
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("rental_asset_id", sa.Uuid(), nullable=False),
        sa.Column("asset_number_snapshot", sa.String(length=32), nullable=False),
        sa.Column("title_snapshot", sa.String(length=255), nullable=False),
        sa.Column("agreed_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("discount", sa.Numeric(12, 2), nullable=False),
        sa.Column("charged_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("returned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("return_note", sa.Text(), nullable=True),
        sa.Column("status", item_status, nullable=False),
        *_base_columns(),
        sa.CheckConstraint("agreed_price >= 0", name="ck_rental_order_items_price"),
        sa.CheckConstraint("discount >= 0", name="ck_rental_order_items_discount"),
        sa.CheckConstraint(
            "charged_amount IS NULL OR charged_amount >= 0",
            name="ck_rental_order_items_charged",
        ),
        sa.ForeignKeyConstraint(["order_id"], ["rental_orders.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["rental_asset_id"],
            ["rental_assets.id"],
            ondelete="RESTRICT",
        ),
        *_audit_foreign_keys(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "order_id",
            "rental_asset_id",
            name="uq_rental_order_items_order_asset",
        ),
    )
    op.create_index(
        op.f("ix_rental_order_items_order_id"),
        "rental_order_items",
        ["order_id"],
    )
    op.create_index(
        op.f("ix_rental_order_items_rental_asset_id"),
        "rental_order_items",
        ["rental_asset_id"],
    )
    op.create_index(
        op.f("ix_rental_order_items_status"),
        "rental_order_items",
        ["status"],
    )


def downgrade() -> None:
    """Drop rental order persistence and its database-owned types."""
    op.drop_index(op.f("ix_rental_order_items_status"), table_name="rental_order_items")
    op.drop_index(
        op.f("ix_rental_order_items_rental_asset_id"),
        table_name="rental_order_items",
    )
    op.drop_index(op.f("ix_rental_order_items_order_id"), table_name="rental_order_items")
    op.drop_table("rental_order_items")
    op.drop_index(op.f("ix_rental_orders_status"), table_name="rental_orders")
    op.drop_index(op.f("ix_rental_orders_customer_id"), table_name="rental_orders")
    op.drop_index(op.f("ix_rental_orders_order_number"), table_name="rental_orders")
    op.drop_table("rental_orders")
    postgresql.ENUM(name="rental_order_item_status").drop(
        op.get_bind(),
        checkfirst=True,
    )
    postgresql.ENUM(name="rental_order_status").drop(op.get_bind(), checkfirst=True)
    op.execute("DROP SEQUENCE rental_order_number_seq")
