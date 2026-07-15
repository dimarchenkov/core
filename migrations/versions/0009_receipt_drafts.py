"""Create draft receipt foundation.

Revision ID: 0009_receipt_drafts
Revises: 0008_create_suppliers
Create Date: 2026-07-15 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_receipt_drafts"
down_revision: str | None = "0008_create_suppliers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create draft receipts, their lines, number sequence, and status enum."""
    receipt_status = postgresql.ENUM(
        "draft",
        "posted",
        "cancelled",
        name="receipt_status",
        create_type=False,
    )
    receipt_status.create(op.get_bind(), checkfirst=True)
    op.execute("CREATE SEQUENCE receipt_number_seq START WITH 1")
    op.create_table(
        "receipts",
        sa.Column("number", sa.String(length=64), nullable=False),
        sa.Column("supplier_id", sa.Uuid(), nullable=False),
        sa.Column("receipt_date", sa.Date(), nullable=False),
        sa.Column("status", receipt_status, server_default="draft", nullable=False),
        sa.Column("source_document_number", sa.String(length=255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["deleted_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("number"),
    )
    op.create_index(op.f("ix_receipts_number"), "receipts", ["number"])
    op.create_index(op.f("ix_receipts_supplier_id"), "receipts", ["supplier_id"])
    op.create_table(
        "receipt_items",
        sa.Column("receipt_id", sa.Uuid(), nullable=False),
        sa.Column("variant_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("purchase_price", sa.Numeric(precision=12, scale=2), nullable=False),
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
        sa.ForeignKeyConstraint(["receipt_id"], ["receipts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["variant_id"], ["catalog_variants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["deleted_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint("quantity > 0", name="ck_receipt_items_quantity_positive"),
        sa.CheckConstraint(
            "purchase_price >= 0",
            name="ck_receipt_items_purchase_price_nonnegative",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_receipt_items_receipt_id"), "receipt_items", ["receipt_id"])
    op.create_index(op.f("ix_receipt_items_variant_id"), "receipt_items", ["variant_id"])


def downgrade() -> None:
    """Drop receipt lines before their receipts, sequence, and lifecycle enum."""
    op.drop_index(op.f("ix_receipt_items_variant_id"), table_name="receipt_items")
    op.drop_index(op.f("ix_receipt_items_receipt_id"), table_name="receipt_items")
    op.drop_table("receipt_items")
    op.drop_index(op.f("ix_receipts_supplier_id"), table_name="receipts")
    op.drop_index(op.f("ix_receipts_number"), table_name="receipts")
    op.drop_table("receipts")
    op.execute("DROP SEQUENCE receipt_number_seq")
    postgresql.ENUM(name="receipt_status").drop(op.get_bind(), checkfirst=True)
