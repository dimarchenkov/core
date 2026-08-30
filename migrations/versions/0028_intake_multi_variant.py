"""Allow Intake variants to reference a draft Product root.

Revision ID: 0028_intake_multi_variant
Revises: 0027_variant_barcodes
Create Date: 2026-08-30 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028_intake_multi_variant"
down_revision: str | None = "0027_variant_barcodes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the safe draft Product reference and make Variant photos optional."""
    op.add_column(
        "intake_item_drafts",
        sa.Column("draft_product_item_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_intake_item_drafts_draft_product_item_id",
        "intake_item_drafts",
        "intake_item_drafts",
        ["draft_product_item_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        op.f("ix_intake_item_drafts_draft_product_item_id"),
        "intake_item_drafts",
        ["draft_product_item_id"],
    )
    op.drop_constraint(
        "ck_intake_item_drafts_new_image_required",
        "intake_item_drafts",
        type_="check",
    )
    op.drop_constraint(
        "ck_intake_item_drafts_new_variant_product_required",
        "intake_item_drafts",
        type_="check",
    )
    op.create_check_constraint(
        "ck_intake_item_drafts_new_product_image_required",
        "intake_item_drafts",
        "kind != 'new_product' OR image_id IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_intake_item_drafts_new_variant_product_required",
        "intake_item_drafts",
        "kind != 'new_variant' OR product_id IS NOT NULL OR draft_product_item_id IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_intake_item_drafts_new_variant_product_exclusive",
        "intake_item_drafts",
        "kind != 'new_variant' OR product_id IS NULL OR draft_product_item_id IS NULL",
    )


def downgrade() -> None:
    """Restore the one-photo-per-new-item Intake schema."""
    op.drop_constraint(
        "ck_intake_item_drafts_new_variant_product_exclusive",
        "intake_item_drafts",
        type_="check",
    )
    op.drop_constraint(
        "ck_intake_item_drafts_new_variant_product_required",
        "intake_item_drafts",
        type_="check",
    )
    op.drop_constraint(
        "ck_intake_item_drafts_new_product_image_required",
        "intake_item_drafts",
        type_="check",
    )
    op.create_check_constraint(
        "ck_intake_item_drafts_new_variant_product_required",
        "intake_item_drafts",
        "kind != 'new_variant' OR product_id IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_intake_item_drafts_new_image_required",
        "intake_item_drafts",
        "kind = 'existing_variant' OR image_id IS NOT NULL",
    )
    op.drop_index(
        op.f("ix_intake_item_drafts_draft_product_item_id"),
        table_name="intake_item_drafts",
    )
    op.drop_constraint(
        "fk_intake_item_drafts_draft_product_item_id",
        "intake_item_drafts",
        type_="foreignkey",
    )
    op.drop_column("intake_item_drafts", "draft_product_item_id")
