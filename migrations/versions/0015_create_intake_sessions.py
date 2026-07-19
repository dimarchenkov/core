"""Create resumable intake sessions and their item drafts.

Revision ID: 0015_create_intake_sessions
Revises: 0014_create_publications
Create Date: 2026-07-19 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015_create_intake_sessions"
down_revision: str | None = "0014_create_publications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _tracking_columns() -> list[sa.Column]:
    """Return the shared BaseModel columns used by intake tables."""
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


def _tracking_constraints() -> list[sa.ForeignKeyConstraint | sa.PrimaryKeyConstraint]:
    """Return attribution and primary-key constraints shared by intake tables."""
    return [
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["deleted_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    ]


def upgrade() -> None:
    """Create Photo First workspaces that precede formal Receipt documents."""
    session_status = postgresql.ENUM(
        "draft",
        "completed",
        "abandoned",
        name="intake_session_status",
        create_type=False,
    )
    item_kind = postgresql.ENUM(
        "existing_variant",
        "new_variant",
        "new_product",
        name="intake_item_kind",
        create_type=False,
    )
    session_status.create(op.get_bind(), checkfirst=True)
    item_kind.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "intake_sessions",
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("status", session_status, server_default="draft", nullable=False),
        sa.Column("supplier_id", sa.Uuid(), nullable=True),
        sa.Column("receipt_id", sa.Uuid(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("abandoned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("abandonment_reason", sa.Text(), nullable=True),
        *_tracking_columns(),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["receipt_id"], ["receipts.id"], ondelete="RESTRICT"),
        *_tracking_constraints(),
        sa.UniqueConstraint("receipt_id"),
    )
    op.create_index(op.f("ix_intake_sessions_owner_id"), "intake_sessions", ["owner_id"])
    op.create_index(op.f("ix_intake_sessions_status"), "intake_sessions", ["status"])
    op.create_index(
        op.f("ix_intake_sessions_supplier_id"),
        "intake_sessions",
        ["supplier_id"],
    )

    op.create_table(
        "intake_item_drafts",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("kind", item_kind, nullable=False),
        sa.Column("variant_id", sa.Uuid(), nullable=True),
        sa.Column("product_id", sa.Uuid(), nullable=True),
        sa.Column("image_id", sa.Uuid(), nullable=True),
        sa.Column("category_id", sa.Uuid(), nullable=True),
        sa.Column("product_title", sa.String(length=255), nullable=True),
        sa.Column("product_description", sa.Text(), nullable=True),
        sa.Column("variant_title", sa.String(length=255), nullable=True),
        sa.Column("attributes", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=True),
        sa.Column("purchase_price", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("abandoned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("abandonment_reason", sa.Text(), nullable=True),
        *_tracking_columns(),
        sa.CheckConstraint(
            "quantity IS NULL OR quantity > 0",
            name="ck_intake_item_drafts_quantity_positive",
        ),
        sa.CheckConstraint(
            "purchase_price IS NULL OR purchase_price >= 0",
            name="ck_intake_item_drafts_purchase_price_nonnegative",
        ),
        sa.CheckConstraint(
            "kind != 'existing_variant' OR variant_id IS NOT NULL",
            name="ck_intake_item_drafts_existing_variant_required",
        ),
        sa.CheckConstraint(
            "kind = 'existing_variant' OR image_id IS NOT NULL",
            name="ck_intake_item_drafts_new_image_required",
        ),
        sa.CheckConstraint(
            "kind != 'new_variant' OR product_id IS NOT NULL",
            name="ck_intake_item_drafts_new_variant_product_required",
        ),
        sa.ForeignKeyConstraint(["session_id"], ["intake_sessions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["variant_id"], ["catalog_variants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["product_id"], ["catalog_products.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["image_id"], ["images.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="RESTRICT"),
        *_tracking_constraints(),
    )
    for column_name in (
        "session_id",
        "kind",
        "variant_id",
        "product_id",
        "image_id",
        "category_id",
    ):
        op.create_index(
            op.f(f"ix_intake_item_drafts_{column_name}"),
            "intake_item_drafts",
            [column_name],
        )


def downgrade() -> None:
    """Remove resumable intake data before dropping its enum types."""
    for column_name in (
        "category_id",
        "image_id",
        "product_id",
        "variant_id",
        "kind",
        "session_id",
    ):
        op.drop_index(
            op.f(f"ix_intake_item_drafts_{column_name}"),
            table_name="intake_item_drafts",
        )
    op.drop_table("intake_item_drafts")
    op.drop_index(op.f("ix_intake_sessions_supplier_id"), table_name="intake_sessions")
    op.drop_index(op.f("ix_intake_sessions_status"), table_name="intake_sessions")
    op.drop_index(op.f("ix_intake_sessions_owner_id"), table_name="intake_sessions")
    op.drop_table("intake_sessions")
    postgresql.ENUM(name="intake_item_kind").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="intake_session_status").drop(op.get_bind(), checkfirst=True)
