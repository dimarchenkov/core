"""Add versioned Rental tariffs owned by CatalogVariant.

Revision ID: 0038_rental_tariffs
Revises: 0037_checkout_fiscal_choice
Create Date: 2026-10-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0038_rental_tariffs"
down_revision: str | None = "0037_checkout_fiscal_choice"
branch_labels: str | None = None
depends_on: str | None = None

rental_tariff_type = sa.Enum("duration", "slot", name="rental_tariff_type")
rental_tariff_status = sa.Enum(
    "active", "superseded", "archived", name="rental_tariff_status"
)


def upgrade() -> None:
    """Create immutable tariff version storage and selectable-schedule indexes."""
    op.create_table(
        "rental_tariffs",
        sa.Column("variant_id", sa.Uuid(), nullable=False),
        sa.Column("lineage_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("type", rental_tariff_type, nullable=False),
        sa.Column("price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("slot_start", sa.Time(), nullable=True),
        sa.Column("slot_end", sa.Time(), nullable=True),
        sa.Column("grace_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deposit_amount", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column(
            "requires_agreement", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column(
            "status",
            rental_tariff_status,
            nullable=False,
            server_default="active",
        ),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("updated_by_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint("price >= 0", name="ck_rental_tariffs_price_nonnegative"),
        sa.CheckConstraint("grace_minutes >= 0", name="ck_rental_tariffs_grace_nonnegative"),
        sa.CheckConstraint(
            "deposit_amount IS NULL OR deposit_amount >= 0",
            name="ck_rental_tariffs_deposit_nonnegative",
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from",
            name="ck_rental_tariffs_valid_period",
        ),
        sa.CheckConstraint(
            "(type = 'duration' AND duration_minutes > 0 "
            "AND slot_start IS NULL AND slot_end IS NULL) OR "
            "(type = 'slot' AND duration_minutes IS NULL "
            "AND slot_start IS NOT NULL AND slot_end IS NOT NULL "
            "AND slot_end > slot_start)",
            name="ck_rental_tariffs_type_fields",
        ),
        sa.ForeignKeyConstraint(
            ["variant_id"], ["catalog_variants.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["deleted_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "lineage_id", "version", name="uq_rental_tariffs_lineage_version"
        ),
    )
    op.create_index("ix_rental_tariffs_variant_id", "rental_tariffs", ["variant_id"])
    op.create_index("ix_rental_tariffs_lineage_id", "rental_tariffs", ["lineage_id"])
    op.create_index("ix_rental_tariffs_valid_from", "rental_tariffs", ["valid_from"])
    op.create_index("ix_rental_tariffs_valid_to", "rental_tariffs", ["valid_to"])
    op.create_index("ix_rental_tariffs_status", "rental_tariffs", ["status"])
    op.create_index(
        "ix_rental_tariffs_variant_selectable",
        "rental_tariffs",
        ["variant_id", "status", "valid_from", "valid_to"],
    )


def downgrade() -> None:
    """Remove Rental tariff storage."""
    op.drop_index("ix_rental_tariffs_variant_selectable", table_name="rental_tariffs")
    op.drop_index("ix_rental_tariffs_status", table_name="rental_tariffs")
    op.drop_index("ix_rental_tariffs_valid_to", table_name="rental_tariffs")
    op.drop_index("ix_rental_tariffs_valid_from", table_name="rental_tariffs")
    op.drop_index("ix_rental_tariffs_lineage_id", table_name="rental_tariffs")
    op.drop_index("ix_rental_tariffs_variant_id", table_name="rental_tariffs")
    op.drop_table("rental_tariffs")
    rental_tariff_status.drop(op.get_bind(), checkfirst=True)
    rental_tariff_type.drop(op.get_bind(), checkfirst=True)
