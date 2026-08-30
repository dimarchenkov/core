"""Reserve stable label identity for new Intake variants.

Revision ID: 0029_intake_label_identity
Revises: 0028_intake_multi_variant
Create Date: 2026-08-30 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029_intake_label_identity"
down_revision: str | None = "0028_intake_multi_variant"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Persist non-reusable SKU and internal barcode reservations on drafts."""
    op.add_column("intake_item_drafts", sa.Column("reserved_sku", sa.String(64)))
    op.add_column(
        "intake_item_drafts",
        sa.Column("reserved_internal_barcode", sa.String(22)),
    )
    op.create_unique_constraint(
        "uq_intake_item_drafts_reserved_sku",
        "intake_item_drafts",
        ["reserved_sku"],
    )
    op.create_unique_constraint(
        "uq_intake_item_drafts_reserved_internal_barcode",
        "intake_item_drafts",
        ["reserved_internal_barcode"],
    )


def downgrade() -> None:
    """Remove draft identity reservations."""
    op.drop_constraint(
        "uq_intake_item_drafts_reserved_internal_barcode",
        "intake_item_drafts",
        type_="unique",
    )
    op.drop_constraint(
        "uq_intake_item_drafts_reserved_sku",
        "intake_item_drafts",
        type_="unique",
    )
    op.drop_column("intake_item_drafts", "reserved_internal_barcode")
    op.drop_column("intake_item_drafts", "reserved_sku")
