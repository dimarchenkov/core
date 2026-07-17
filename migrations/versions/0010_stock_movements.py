"""Create immutable stock movement ledger.

Revision ID: 0010_stock_movements
Revises: 0009_receipt_drafts
Create Date: 2026-07-17 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_stock_movements"
down_revision: str | None = "0009_receipt_drafts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the immutable stock movement ledger and its supporting enums."""
    movement_type = postgresql.ENUM(
        "receipt",
        "sale",
        "return",
        "adjustment",
        "write_off",
        "transfer_in",
        "transfer_out",
        name="movement_type",
        create_type=False,
    )
    source_type = postgresql.ENUM(
        "receipt",
        "sale",
        "inventory",
        "system",
        name="source_type",
        create_type=False,
    )
    movement_type.create(op.get_bind(), checkfirst=True)
    source_type.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "stock_movements",
        sa.Column("variant_id", sa.Uuid(), nullable=False),
        sa.Column("movement_type", movement_type, nullable=False),
        sa.Column("quantity_delta", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("source_type", source_type, nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
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
        sa.ForeignKeyConstraint(["variant_id"], ["catalog_variants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["deleted_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint("quantity_delta <> 0", name="ck_stock_movements_quantity_delta_nonzero"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_stock_movements_variant_id"), "stock_movements", ["variant_id"])
    op.create_index(op.f("ix_stock_movements_source_id"), "stock_movements", ["source_id"])


def downgrade() -> None:
    """Drop the movement ledger before removing its PostgreSQL enum types."""
    op.drop_index(op.f("ix_stock_movements_source_id"), table_name="stock_movements")
    op.drop_index(op.f("ix_stock_movements_variant_id"), table_name="stock_movements")
    op.drop_table("stock_movements")
    postgresql.ENUM(name="source_type").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="movement_type").drop(op.get_bind(), checkfirst=True)
